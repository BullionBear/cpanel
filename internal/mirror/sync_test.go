package mirror

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BullionBear/cpanel/internal/classify"
	"github.com/BullionBear/cpanel/internal/mftiko"
	"github.com/BullionBear/cpanel/internal/platform/pgtest"
	"github.com/BullionBear/cpanel/internal/reconcile"
	"github.com/BullionBear/cpanel/internal/worker"
	"github.com/BullionBear/cpanel/migrations"
)

func testDB(t *testing.T) (context.Context, *pgxpool.Pool) {
	t.Helper()
	srv := pgtest.Start(t)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	t.Cleanup(cancel)
	if _, err := migrations.Up(ctx, srv.DSN()); err != nil {
		t.Fatal(err)
	}
	pool, err := pgxpool.New(ctx, srv.DSN())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	return ctx, pool
}

type accountRow struct {
	Name, Kind          string
	Exchange, UID       *string
	Revision            int64
	Deleted             *time.Time
	Conflict            []byte
	FirstSeen, LastSeen time.Time
}

func accounts(t *testing.T, ctx context.Context, db *pgxpool.Pool) map[int64]accountRow {
	t.Helper()
	rows, err := db.Query(ctx, `
		SELECT api_id, name, kind, exchange, exch_account_uid, account_revision, deleted_at, identity_conflict, first_seen_at, last_seen_at
		FROM mirror.account ORDER BY api_id`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	out := map[int64]accountRow{}
	for rows.Next() {
		var id int64
		var row accountRow
		if err := rows.Scan(&id, &row.Name, &row.Kind, &row.Exchange, &row.UID, &row.Revision, &row.Deleted, &row.Conflict, &row.FirstSeen, &row.LastSeen); err != nil {
			t.Fatal(err)
		}
		out[id] = row
	}
	if err := rows.Err(); err != nil {
		t.Fatal(err)
	}
	return out
}

func enqueue(t *testing.T, ctx context.Context, db *pgxpool.Pool) {
	t.Helper()
	if _, err := db.Exec(ctx, `
		INSERT INTO ingest.periodic (name, kind, lease_key, period, next_due_at)
		VALUES ('mftiko.sync', 'mftiko.sync', 'global:mftiko', interval '1 minute', now())
		ON CONFLICT (name) DO NOTHING`); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(ctx, `
		INSERT INTO ingest.job (kind, dedup_key, lease_key, periodic_name)
		VALUES ('mftiko.sync', 'periodic:mftiko.sync', 'global:mftiko', 'mftiko.sync')`); err != nil {
		t.Fatal(err)
	}
}

func startWorker(t *testing.T, w *worker.Worker) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		w.Run(ctx)
	}()
	t.Cleanup(func() {
		cancel()
		select {
		case <-done:
		case <-time.After(30 * time.Second):
			t.Error("worker did not stop")
		}
	})
}

func waitJob(t *testing.T, ctx context.Context, db *pgxpool.Pool, n int) (status, class string) {
	t.Helper()
	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) {
		err := db.QueryRow(ctx, `
			SELECT status, coalesce(error_class::text, '')
			FROM ingest.job_run WHERE kind = 'mftiko.sync'
			ORDER BY finished_at ASC, id ASC LIMIT 1 OFFSET $1`, n-1).Scan(&status, &class)
		if err == nil {
			return status, class
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("job_run %d did not finish", n)
	return "", ""
}

func TestSyncCopiesPagesKindsAndDeleted(t *testing.T) {
	ctx, db := testDB(t)
	deleted := "2026-11-01T00:00:00Z"
	var mu sync.Mutex
	script := []string{
		fmt.Sprintf(`{"freshness":{"source":"primary"},"next_cursor":"p2","items":[
			{"api_id":1,"name":"spot","kind":"Binance","exchange":" binance ","exch_account_uid":" u1 ","revision":5,"referenced_by":[],"settings":{}},
			{"api_id":2,"name":"pm","kind":"BinancePM","exchange":"binance","exch_account_uid":"u2","revision":2,"deleted_at":%q,"referenced_by":[],"settings":{}}
		]}`, deleted),
		`{"freshness":{"source":"primary"},"items":[
			{"api_id":3,"name":"eo","kind":"BinanceEO","exchange":"binance","exch_account_uid":"u3","revision":1,"referenced_by":[],"settings":{}},
			{"api_id":4,"name":"odd","kind":"NotAKind","revision":1,"referenced_by":[],"settings":{}}
		]}`,
	}
	var calls int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			t.Errorf("method %s", r.Method)
		}
		w.Header().Set("Content-Type", "application/json")
		mu.Lock()
		defer mu.Unlock()
		if calls >= len(script) {
			http.Error(w, "extra", http.StatusInternalServerError)
			return
		}
		io.WriteString(w, script[calls])
		calls++
	}))
	t.Cleanup(srv.Close)
	client, err := mftiko.New(srv.URL, "tok", srv.Client())
	if err != nil {
		t.Fatal(err)
	}
	enqueue(t, ctx, db)
	startWorker(t, &worker.Worker{
		DB: db, Name: "w", Slots: 1, Poll: 20 * time.Millisecond, BudgetTime: time.Minute,
		Handlers: worker.Handlers{reconcile.MftikoSync.Name: MftikoSync(client)},
		Local:    []string{reconcile.MftikoSync.Name},
	})
	if status, class := waitJob(t, ctx, db, 1); status != "succeeded" || class != "" {
		t.Fatalf("first run %s %s", status, class)
	}
	got := accounts(t, ctx, db)
	if len(got) != 4 {
		t.Fatalf("%d accounts", len(got))
	}
	if got[1].Exchange == nil || *got[1].Exchange != "binance" || got[1].UID == nil || *got[1].UID != "u1" || got[1].Revision != 5 {
		t.Fatalf("identity %+v", got[1])
	}
	if got[2].Kind != "BinancePM" || got[2].Deleted == nil || !got[2].Deleted.Equal(time.Date(2026, 11, 1, 0, 0, 0, 0, time.UTC)) {
		t.Fatalf("deleted %+v", got[2])
	}
	if got[3].Kind != "BinanceEO" || got[4].Kind != "NotAKind" || got[4].Exchange != nil || got[4].UID != nil {
		t.Fatalf("kinds %+v %+v", got[3], got[4])
	}
	var items int
	var complete *time.Time
	if err := db.QueryRow(ctx, `SELECT items, last_complete_at FROM mirror.sync_state WHERE source = 'mftiko.accounts'`).Scan(&items, &complete); err != nil {
		t.Fatal(err)
	}
	if items != 4 || complete == nil {
		t.Fatalf("items %d complete %v", items, complete)
	}

	// An older revision does not move the row backwards. A different uid is refused.
	mu.Lock()
	script = []string{`{"freshness":{"source":"primary"},"items":[
		{"api_id":1,"name":"renamed","kind":"Binance","exchange":"bybit","exch_account_uid":"other","revision":4,"referenced_by":[],"settings":{}},
		{"api_id":2,"name":"pm","kind":"BinancePM","exchange":"binance","exch_account_uid":"u2","revision":3,"referenced_by":[],"settings":{}}
	]}`}
	calls = 0
	mu.Unlock()
	enqueue(t, ctx, db)
	if status, class := waitJob(t, ctx, db, 2); status != "succeeded" || class != "" {
		t.Fatalf("second run %s %s", status, class)
	}
	got = accounts(t, ctx, db)
	if got[1].Name != "spot" || got[1].Revision != 5 || got[1].UID == nil || *got[1].UID != "u1" || got[1].Exchange == nil || *got[1].Exchange != "binance" || len(got[1].Conflict) == 0 {
		t.Fatalf("conflict %+v", got[1])
	}
	var refused struct {
		Exchange string `json:"exchange"`
		UID      string `json:"exch_account_uid"`
	}
	if err := json.Unmarshal(got[1].Conflict, &refused); err != nil || refused.Exchange != "bybit" || refused.UID != "other" {
		t.Fatalf("conflict json %s", got[1].Conflict)
	}
	if got[2].Deleted != nil || got[2].Revision != 3 {
		t.Fatalf("undeleted %+v", got[2])
	}
	var conflicts int
	if err := db.QueryRow(ctx, `SELECT count(*) FROM mirror.account WHERE identity_conflict IS NOT NULL`).Scan(&conflicts); err != nil {
		t.Fatal(err)
	}
	if conflicts != 1 {
		t.Fatalf("conflicts %d", conflicts)
	}

	before := accounts(t, ctx, db)
	srv.Close()
	enqueue(t, ctx, db)
	status, class := waitJob(t, ctx, db, 3)
	if status != "failed" || class != "unavailable" {
		t.Fatalf("down run %s %s", status, class)
	}
	after := accounts(t, ctx, db)
	if len(after) != len(before) {
		t.Fatalf("rows %d, want %d", len(after), len(before))
	}
	for id, row := range before {
		got := after[id]
		if got.Name != row.Name || got.Revision != row.Revision || !got.LastSeen.Equal(row.LastSeen) || string(got.Conflict) != string(row.Conflict) {
			t.Fatalf("api_id %d changed: %+v -> %+v", id, row, got)
		}
	}
}

func TestPageLimitDoesNotComplete(t *testing.T) {
	ctx, db := testDB(t)
	var calls atomic.Int32
	client := pageClient{next: func() (mftiko.Page, error) {
		n := calls.Add(1)
		return mftiko.Page{Accounts: []mftiko.Account{{APIID: int64(n), Name: "a", Kind: "Binance", Revision: 1}}, Next: "more"}, nil
	}}
	enqueue(t, ctx, db)
	startWorker(t, &worker.Worker{
		DB: db, Name: "w", Slots: 1, Poll: 20 * time.Millisecond, BudgetTime: time.Minute,
		Handlers: worker.Handlers{reconcile.MftikoSync.Name: MftikoSync(client)},
	})
	status, class := waitJob(t, ctx, db, 1)
	if status != "failed" || class != string(classify.Internal) {
		t.Fatalf("%s %s", status, class)
	}
	if got := int(calls.Load()); got != maxPages {
		t.Fatalf("requests %d, want %d", got, maxPages)
	}
	var complete *time.Time
	if err := db.QueryRow(ctx, `SELECT last_complete_at FROM mirror.sync_state WHERE source = 'mftiko.accounts'`).Scan(&complete); err != nil {
		t.Fatal(err)
	}
	if complete != nil {
		t.Fatal("incomplete round set last_complete_at")
	}
}

func TestEgressPauseDoesNotHoldSync(t *testing.T) {
	ctx, db := testDB(t)
	if _, err := db.Exec(ctx, `INSERT INTO ingest.pause (scope, until, reason) VALUES ('egress:test', now() + interval '1 hour', '418')`); err != nil {
		t.Fatal(err)
	}
	enqueue(t, ctx, db)
	if _, err := db.Exec(ctx, `
		INSERT INTO ingest.job (kind, dedup_key, lease_key) VALUES ('trade.history', 'stream:9', 'account:9')`); err != nil {
		t.Fatal(err)
	}
	var ranTrade atomic.Bool
	client := pageClient{next: func() (mftiko.Page, error) {
		return mftiko.Page{Accounts: []mftiko.Account{{APIID: 1, Name: "a", Kind: "BinanceUM", Revision: 1}}}, nil
	}}
	startWorker(t, &worker.Worker{
		DB: db, Name: "w", Slots: 1, Poll: 20 * time.Millisecond, BudgetTime: time.Minute, Egress: "test",
		Local: []string{reconcile.MftikoSync.Name},
		Handlers: worker.Handlers{
			reconcile.MftikoSync.Name: MftikoSync(client),
			"trade.history": func(context.Context, *worker.Job) (worker.Result, error) {
				ranTrade.Store(true)
				return worker.Result{}, nil
			},
		},
	})
	if status, _ := waitJob(t, ctx, db, 1); status != "succeeded" {
		t.Fatalf("sync %s", status)
	}
	time.Sleep(100 * time.Millisecond)
	if ranTrade.Load() {
		t.Fatal("trade.history ran during an egress pause")
	}
	var pending int
	if err := db.QueryRow(ctx, `SELECT count(*) FROM ingest.job WHERE kind = 'trade.history' AND status = 'pending'`).Scan(&pending); err != nil {
		t.Fatal(err)
	}
	if pending != 1 {
		t.Fatalf("trade job pending %d", pending)
	}
}

type pageClient struct {
	next func() (mftiko.Page, error)
}

func (p pageClient) ListAccounts(context.Context, string) (mftiko.Page, error) { return p.next() }
