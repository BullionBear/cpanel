package mirror

import (
	"context"
	"net"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"connectrpc.com/connect"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"golang.org/x/net/http2"
	"golang.org/x/net/http2/h2c"

	symv1 "github.com/BullionBear/mftiko/contract/gen/mftiko/sym/v1"
	"github.com/BullionBear/mftiko/contract/gen/mftiko/sym/v1/symv1connect"

	"github.com/BullionBear/cpanel/internal/classify"
	"github.com/BullionBear/cpanel/internal/reconcile"
	"github.com/BullionBear/cpanel/internal/sym"
	"github.com/BullionBear/cpanel/internal/worker"
)

type tableKey struct{ venue, category string }

// symBook is a SYM that only accepts the connect handler's gRPC calls.
type symBook struct {
	symv1connect.UnimplementedSymServiceHandler
	mu         sync.Mutex
	rev        map[tableKey]int64
	active     map[tableKey][]*symv1.SymbolInfo
	terminal   map[uint32]*symv1.SymbolInfo
	bumpOnList bool
	down       bool
}

func newBook() *symBook {
	return &symBook{
		rev: map[tableKey]int64{}, active: map[tableKey][]*symv1.SymbolInfo{}, terminal: map[uint32]*symv1.SymbolInfo{},
	}
}

func (b *symBook) set(venue, category string, revision int64, rows ...*symv1.SymbolInfo) {
	b.mu.Lock()
	defer b.mu.Unlock()
	k := tableKey{venue, category}
	b.rev[k] = revision
	b.active[k] = append([]*symv1.SymbolInfo(nil), rows...)
}

func (b *symBook) delist(venue, category string, revision int64, uid uint32, row *symv1.SymbolInfo) {
	b.mu.Lock()
	defer b.mu.Unlock()
	k := tableKey{venue, category}
	b.rev[k] = revision
	kept := make([]*symv1.SymbolInfo, 0, len(b.active[k]))
	for _, info := range b.active[k] {
		if info.GetUid() != uid {
			kept = append(kept, info)
		}
	}
	b.active[k] = kept
	b.terminal[uid] = row
}

func (b *symBook) Revisions(context.Context, *connect.Request[symv1.RevisionsRequest]) (*connect.Response[symv1.RevisionsResponse], error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.down {
		return nil, connect.NewError(connect.CodeUnavailable, http.ErrServerClosed)
	}
	tables := make([]*symv1.TableRevision, 0, len(b.rev))
	for k, rev := range b.rev {
		tables = append(tables, &symv1.TableRevision{Venue: k.venue, Category: k.category, Revision: rev})
	}
	return connect.NewResponse(&symv1.RevisionsResponse{Revisions: &symv1.RevisionList{Tables: tables}}), nil
}

func (b *symBook) List(_ context.Context, req *connect.Request[symv1.ListRequest]) (*connect.Response[symv1.ListResponse], error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	k := tableKey{req.Msg.GetVenue(), req.Msg.GetCategory()}
	if b.bumpOnList {
		b.rev[k]++
		b.bumpOnList = false
	}
	rows := b.active[k]
	if req.Msg.GetOffset() > 0 {
		rows = nil
	}
	return connect.NewResponse(&symv1.ListResponse{List: &symv1.SymbolList{Symbols: rows, Total: uint32(len(b.active[k]))}}), nil
}

func (b *symBook) GetByUid(_ context.Context, req *connect.Request[symv1.GetByUidRequest]) (*connect.Response[symv1.GetByUidResponse], error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if req.Msg.GetIncludeInactive() {
		if info := b.terminal[req.Msg.GetUid()]; info != nil {
			return connect.NewResponse(&symv1.GetByUidResponse{Info: info}), nil
		}
	}
	for _, rows := range b.active {
		for _, info := range rows {
			if info.GetUid() == req.Msg.GetUid() {
				return connect.NewResponse(&symv1.GetByUidResponse{Info: info}), nil
			}
		}
	}
	return nil, connect.NewError(connect.CodeNotFound, errNotFound)
}

var errNotFound = http.ErrNoCookie

func serveBook(t *testing.T, book *symBook) string {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	path, handler := symv1connect.NewSymServiceHandler(book)
	mux := http.NewServeMux()
	mux.Handle(path, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.Header.Get("Content-Type"), "application/grpc") {
			http.Error(w, "grpc only", http.StatusUnsupportedMediaType)
			return
		}
		handler.ServeHTTP(w, r)
	}))
	srv := &http.Server{Handler: h2c.NewHandler(mux, &http2.Server{})}
	go func() { _ = srv.Serve(ln) }()
	t.Cleanup(func() { _ = srv.Close() })
	return "http://" + ln.Addr().String()
}

func inst(uid uint32, ticker, exch string, status symv1.SymbolStatus, updated int64) *symv1.SymbolInfo {
	size := 0.001
	tick := 0.1
	return &symv1.SymbolInfo{
		Uid: uid, UniversalTicker: ticker, ExchTicker: exch, Base: "BTC", Quote: "USDT",
		ContractSize: &size, Status: status, UpdatedAtNs: updated,
		Filters: map[string]*symv1.Bound{"price_tick": {Value: &tick}, "min_qty": {}},
	}
}

func enqueueSymbol(t *testing.T, ctx context.Context, db *pgxpool.Pool) {
	t.Helper()
	if _, err := db.Exec(ctx, `
		INSERT INTO ingest.periodic (name, kind, lease_key, period, next_due_at)
		VALUES ('sym.sync', 'sym.sync', 'global:sym', interval '1 minute', now())
		ON CONFLICT (name) DO NOTHING`); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(ctx, `
		INSERT INTO ingest.job (kind, dedup_key, lease_key, periodic_name)
		VALUES ('sym.sync', 'periodic:sym.sync', 'global:sym', 'sym.sync')`); err != nil {
		t.Fatal(err)
	}
}

func runSymbol(t *testing.T, ctx context.Context, db *pgxpool.Pool, client Catalog) {
	t.Helper()
	startWorker(t, &worker.Worker{
		DB: db, Name: "w", Slots: 1, Poll: 20 * time.Millisecond, BudgetTime: time.Minute,
		Local:    []string{reconcile.SymSync.Name},
		Handlers: worker.Handlers{reconcile.SymSync.Name: SymSync(client)},
	})
}

func waitSymbol(t *testing.T, ctx context.Context, db *pgxpool.Pool, n int) (status, class string) {
	t.Helper()
	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) {
		err := db.QueryRow(ctx, `
			SELECT status, coalesce(error_class::text, '')
			FROM ingest.job_run WHERE kind = 'sym.sync'
			ORDER BY finished_at ASC, id ASC LIMIT 1 OFFSET $1`, n-1).Scan(&status, &class)
		if err == nil {
			return status, class
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("sym job %d did not finish", n)
	return "", ""
}

type symbolRow struct {
	Venue, Category, Ticker, Status string
	Active                          bool
	Contract                        *string
	Filters                         []byte
}

func symbols(t *testing.T, ctx context.Context, db *pgxpool.Pool) map[int64]symbolRow {
	t.Helper()
	rows, err := db.Query(ctx, `
		SELECT uid, venue, category, exch_ticker, status, active, contract_size::text, filters
		FROM mirror.symbol ORDER BY uid`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	out := map[int64]symbolRow{}
	for rows.Next() {
		var uid int64
		var row symbolRow
		if err := rows.Scan(&uid, &row.Venue, &row.Category, &row.Ticker, &row.Status, &row.Active, &row.Contract, &row.Filters); err != nil {
			t.Fatal(err)
		}
		out[uid] = row
	}
	if err := rows.Err(); err != nil {
		t.Fatal(err)
	}
	return out
}

func tableRevision(t *testing.T, ctx context.Context, db *pgxpool.Pool, source string) (rev, remote *int64, complete bool) {
	t.Helper()
	var done *time.Time
	if err := db.QueryRow(ctx, `
		SELECT revision, remote_revision, last_complete_at FROM mirror.sync_state WHERE source = $1`, source).
		Scan(&rev, &remote, &done); err != nil {
		t.Fatal(err)
	}
	return rev, remote, done != nil
}

func TestSymReloadDelistAndReverse(t *testing.T) {
	ctx, db := testDB(t)
	book := newBook()
	book.set("BinanceUM", "Perp", 5, inst(1, "BinanceUM_Perp_BTCUSDT", "BTCUSDT", symv1.SymbolStatus_SYMBOL_STATUS_TRADABLE, 100))
	book.set("BinanceEO", "Spot", 1, inst(8, "BinanceEO_Spot_BTCUSDT", "BTCUSDT", symv1.SymbolStatus_SYMBOL_STATUS_TRADABLE, 50))
	client, err := sym.New(serveBook(t, book), "tok")
	if err != nil {
		t.Fatal(err)
	}
	enqueueSymbol(t, ctx, db)
	runSymbol(t, ctx, db, client)
	if status, class := waitSymbol(t, ctx, db, 1); status != "succeeded" || class != "" {
		t.Fatalf("first %s %s", status, class)
	}
	got := symbols(t, ctx, db)
	if len(got) != 2 || got[1].Venue != "BinanceUM" || got[1].Status != "TRADABLE" || !got[1].Active || got[8].Venue != "BinanceEO" {
		t.Fatalf("loaded %+v", got)
	}
	if got[1].Contract == nil || *got[1].Contract != "0.001" || !strings.Contains(string(got[1].Filters), `"min_qty": null`) {
		t.Fatalf("numeric or filters %s %s", *got[1].Contract, got[1].Filters)
	}
	rev, remote, complete := tableRevision(t, ctx, db, "sym:BinanceUM:Perp")
	if rev == nil || *rev != 5 || remote == nil || *remote != 5 || !complete {
		t.Fatalf("um revision %v remote %v complete %v", rev, remote, complete)
	}

	book.set("BinanceUM", "Perp", 6,
		inst(1, "BinanceUM_Perp_BTCUSDT", "BTCUSDT", symv1.SymbolStatus_SYMBOL_STATUS_TRADABLE, 100),
		inst(2, "BinanceUM_Perp_ETHUSDT", "ETHUSDT", symv1.SymbolStatus_SYMBOL_STATUS_TRADABLE, 200))
	enqueueSymbol(t, ctx, db)
	if status, class := waitSymbol(t, ctx, db, 2); status != "succeeded" || class != "" {
		t.Fatalf("advance %s %s", status, class)
	}
	got = symbols(t, ctx, db)
	if len(got) != 3 || got[2].Ticker != "ETHUSDT" || !got[2].Active {
		t.Fatalf("after advance %+v", got)
	}
	rev, remote, complete = tableRevision(t, ctx, db, "sym:BinanceUM:Perp")
	if rev == nil || *rev != 6 || remote == nil || *remote != 6 || !complete {
		t.Fatalf("advanced revision %v remote %v complete %v", rev, remote, complete)
	}

	book.delist("BinanceUM", "Perp", 7, 2, inst(2, "BinanceUM_Perp_ETHUSDT", "ETHUSDT", symv1.SymbolStatus_SYMBOL_STATUS_DELISTED, 300))
	enqueueSymbol(t, ctx, db)
	if status, class := waitSymbol(t, ctx, db, 3); status != "succeeded" || class != "" {
		t.Fatalf("delist %s %s", status, class)
	}
	got = symbols(t, ctx, db)
	if got[2].Active || got[2].Status != "DELISTED" || got[2].Ticker != "ETHUSDT" || !got[1].Active {
		t.Fatalf("after delist %+v", got)
	}

	book.set("BinanceUM", "Perp", 8,
		inst(1, "BinanceUM_Perp_BTCUSDT", "BTCUSDT", symv1.SymbolStatus_SYMBOL_STATUS_TRADABLE, 100),
		inst(2, "BinanceUM_Perp_ETHUSDT", "ETHUSDT", symv1.SymbolStatus_SYMBOL_STATUS_TRADABLE, 400),
		inst(3, "BinanceUM_Perp_ETHUSDT", "ETHUSDT", symv1.SymbolStatus_SYMBOL_STATUS_TRADABLE, 500))
	enqueueSymbol(t, ctx, db)
	if status, class := waitSymbol(t, ctx, db, 4); status != "succeeded" || class != "" {
		t.Fatalf("relist %s %s", status, class)
	}
	got = symbols(t, ctx, db)
	if got[2].Status != "DELISTED" || got[2].Active || !got[3].Active || got[3].Ticker != "ETHUSDT" {
		t.Fatalf("terminal status reverted or relist missing %+v", got)
	}
	var uid uint32
	var ok bool
	if err := pgx.BeginFunc(ctx, db, func(tx pgx.Tx) error {
		var err error
		uid, ok, err = LookupUID(ctx, tx, "BinanceUM", "Perp", "ETHUSDT")
		return err
	}); err != nil {
		t.Fatal(err)
	}
	if !ok || uid != 3 {
		t.Fatalf("reverse uid %d ok %v", uid, ok)
	}
}

func TestSymRevisionMovesDuringList(t *testing.T) {
	ctx, db := testDB(t)
	book := newBook()
	book.set("BinanceUM", "Perp", 5,
		inst(1, "BinanceUM_Perp_BTCUSDT", "BTCUSDT", symv1.SymbolStatus_SYMBOL_STATUS_TRADABLE, 100),
		inst(2, "BinanceUM_Perp_ETHUSDT", "ETHUSDT", symv1.SymbolStatus_SYMBOL_STATUS_TRADABLE, 100))
	client, err := sym.New(serveBook(t, book), "tok")
	if err != nil {
		t.Fatal(err)
	}
	enqueueSymbol(t, ctx, db)
	runSymbol(t, ctx, db, client)
	if status, _ := waitSymbol(t, ctx, db, 1); status != "succeeded" {
		t.Fatalf("load %s", status)
	}

	var loadedAt time.Time
	if err := db.QueryRow(ctx, `SELECT last_complete_at FROM mirror.sync_state WHERE source = 'sym:BinanceUM:Perp'`).Scan(&loadedAt); err != nil {
		t.Fatal(err)
	}
	book.mu.Lock()
	book.bumpOnList = true
	book.rev[tableKey{"BinanceUM", "Perp"}] = 6
	book.active[tableKey{"BinanceUM", "Perp"}] = []*symv1.SymbolInfo{
		inst(1, "BinanceUM_Perp_BTCUSDT", "BTCUSDT", symv1.SymbolStatus_SYMBOL_STATUS_TRADABLE, 100),
	}
	book.mu.Unlock()
	enqueueSymbol(t, ctx, db)
	if status, class := waitSymbol(t, ctx, db, 2); status != "succeeded" || class != "" {
		t.Fatalf("moving %s %s", status, class)
	}
	got := symbols(t, ctx, db)
	if !got[2].Active {
		t.Fatal("a row was deactivated from an inconsistent list")
	}
	rev, remote, _ := tableRevision(t, ctx, db, "sym:BinanceUM:Perp")
	var still time.Time
	if err := db.QueryRow(ctx, `SELECT last_complete_at FROM mirror.sync_state WHERE source = 'sym:BinanceUM:Perp'`).Scan(&still); err != nil {
		t.Fatal(err)
	}
	if rev == nil || *rev != 6 || remote == nil || *remote != 7 || !still.Equal(loadedAt) {
		t.Fatalf("revision %v remote %v complete moved from %s to %s", rev, remote, loadedAt, still)
	}

	enqueueSymbol(t, ctx, db)
	if status, _ := waitSymbol(t, ctx, db, 3); status != "succeeded" {
		t.Fatalf("reload %s", status)
	}
	got = symbols(t, ctx, db)
	if got[2].Active || got[2].Ticker != "ETHUSDT" {
		t.Fatalf("after reload %+v", got[2])
	}
	rev, remote, complete := tableRevision(t, ctx, db, "sym:BinanceUM:Perp")
	if rev == nil || *rev != 7 || remote == nil || *remote != 7 || !complete {
		t.Fatalf("reloaded revision %v remote %v complete %v", rev, remote, complete)
	}
}

func TestSymDownLeavesRows(t *testing.T) {
	ctx, db := testDB(t)
	book := newBook()
	book.set("BinanceUM", "Perp", 5, inst(1, "BinanceUM_Perp_BTCUSDT", "BTCUSDT", symv1.SymbolStatus_SYMBOL_STATUS_TRADABLE, 100))
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	path, handler := symv1connect.NewSymServiceHandler(book)
	mux := http.NewServeMux()
	mux.Handle(path, handler)
	srv := &http.Server{Handler: h2c.NewHandler(mux, &http2.Server{})}
	go func() { _ = srv.Serve(ln) }()
	client, err := sym.New("http://"+ln.Addr().String(), "tok")
	if err != nil {
		t.Fatal(err)
	}
	enqueueSymbol(t, ctx, db)
	runSymbol(t, ctx, db, client)
	if status, _ := waitSymbol(t, ctx, db, 1); status != "succeeded" {
		t.Fatalf("load %s", status)
	}
	before := symbols(t, ctx, db)
	book.mu.Lock()
	book.down = true
	book.mu.Unlock()
	_ = srv.Close()
	enqueueSymbol(t, ctx, db)
	status, class := waitSymbol(t, ctx, db, 2)
	if status != "failed" || class != "unavailable" {
		t.Fatalf("down %s %s", status, class)
	}
	after := symbols(t, ctx, db)
	if len(after) != 1 || after[1].Ticker != before[1].Ticker || after[1].Active != before[1].Active || after[1].Status != before[1].Status {
		t.Fatalf("rows changed %+v -> %+v", before, after)
	}
}

func TestSymbolPageLimitDoesNotComplete(t *testing.T) {
	ctx, db := testDB(t)
	var calls atomic.Int32
	client := pageCatalog{list: func() (sym.Page, error) {
		n := calls.Add(1)
		return sym.Page{Instruments: []sym.Instrument{{
			UID: uint32(n), Venue: "BinanceUM", Category: "Perp", ExchTicker: "BTCUSDT",
			UniversalTicker: "BinanceUM_Perp_BTCUSDT", Base: "BTC", Quote: "USDT", Status: "TRADABLE",
		}}, Total: 1_000_000}, nil
	}}
	enqueueSymbol(t, ctx, db)
	runSymbol(t, ctx, db, client)
	status, class := waitSymbol(t, ctx, db, 1)
	if status != "failed" || class != string(classify.Internal) {
		t.Fatalf("%s %s", status, class)
	}
	if got := int(calls.Load()); got != maxPages {
		t.Fatalf("pages %d, want %d", got, maxPages)
	}
	var complete *time.Time
	if err := db.QueryRow(ctx, `SELECT last_complete_at FROM mirror.sync_state WHERE source = 'sym'`).Scan(&complete); err != nil {
		t.Fatal(err)
	}
	if complete != nil {
		t.Fatal("incomplete round set last_complete_at")
	}
	var n int
	if err := db.QueryRow(ctx, `SELECT count(*) FROM mirror.symbol`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	if n != 0 {
		t.Fatalf("partial list wrote %d rows", n)
	}
}

func TestEgressPauseDoesNotHoldSymbolSync(t *testing.T) {
	ctx, db := testDB(t)
	if _, err := db.Exec(ctx, `INSERT INTO ingest.pause (scope, until, reason) VALUES ('egress:test', now() + interval '1 hour', '418')`); err != nil {
		t.Fatal(err)
	}
	enqueueSymbol(t, ctx, db)
	if _, err := db.Exec(ctx, `
		INSERT INTO ingest.job (kind, dedup_key, lease_key) VALUES ('trade.history', 'stream:9', 'account:9')`); err != nil {
		t.Fatal(err)
	}
	var ranTrade atomic.Bool
	client := pageCatalog{list: func() (sym.Page, error) {
		return sym.Page{Instruments: []sym.Instrument{{
			UID: 1, Venue: "BinanceUM", Category: "Perp", ExchTicker: "BTCUSDT",
			UniversalTicker: "BinanceUM_Perp_BTCUSDT", Base: "BTC", Quote: "USDT", Status: "TRADABLE",
		}}, Total: 1}, nil
	}}
	startWorker(t, &worker.Worker{
		DB: db, Name: "w", Slots: 1, Poll: 20 * time.Millisecond, BudgetTime: time.Minute, Egress: "test",
		Local: []string{reconcile.SymSync.Name},
		Handlers: worker.Handlers{
			reconcile.SymSync.Name: SymSync(client),
			"trade.history": func(context.Context, *worker.Job) (worker.Result, error) {
				ranTrade.Store(true)
				return worker.Result{}, nil
			},
		},
	})
	if status, _ := waitSymbol(t, ctx, db, 1); status != "succeeded" {
		t.Fatalf("sync %s", status)
	}
	time.Sleep(100 * time.Millisecond)
	if ranTrade.Load() {
		t.Fatal("trade.history ran during an egress pause")
	}
}

type pageCatalog struct {
	list func() (sym.Page, error)
}

func (p pageCatalog) Revisions(context.Context) ([]sym.Table, error) {
	return []sym.Table{{Venue: "BinanceUM", Category: "Perp", Revision: 1}}, nil
}

func (p pageCatalog) List(context.Context, string, string, uint32) (sym.Page, error) {
	return p.list()
}

func (p pageCatalog) GetByUid(context.Context, uint32, bool) (sym.Instrument, error) {
	return sym.Instrument{}, &classify.ClassifiedError{Class: classify.NotFound, Detail: "missing"}
}
