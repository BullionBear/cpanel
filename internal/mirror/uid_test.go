package mirror

import (
	"context"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	symv1 "github.com/BullionBear/mftiko/contract/gen/mftiko/sym/v1"

	"github.com/BullionBear/cpanel/internal/classify"
	"github.com/BullionBear/cpanel/internal/sym"
)

func TestScopeUIDFromTerminalTable(t *testing.T) {
	ctx, db := testDB(t)
	book := newBook()
	book.terminal[15] = inst(15, "BinanceCM_Inverse_BTCUSD", "BTCUSD", symv1.SymbolStatus_SYMBOL_STATUS_DELISTED, 1)
	client, err := sym.New(serveBook(t, book), "tok")
	if err != nil {
		t.Fatal(err)
	}
	insertScope(t, ctx, db, 1, 15)
	enqueueSymbol(t, ctx, db)
	runSymbol(t, ctx, db, client)
	if status, class := waitSymbol(t, ctx, db, 1); status != "succeeded" || class != "" {
		t.Fatalf("%s %s", status, class)
	}
	got := symbols(t, ctx, db)
	row, ok := got[15]
	if !ok || row.Active || row.Ticker != "BTCUSD" || row.Venue != "BinanceCM" || row.Category != "Inverse" {
		t.Fatalf("%+v ok %v", row, ok)
	}
	if n := lookupCount(t, ctx, db); n != 0 {
		t.Fatalf("lookup rows %d", n)
	}
}

func TestScopeUIDNotFoundThenFilled(t *testing.T) {
	ctx, db := testDB(t)
	cat := &scriptUID{result: map[uint32]sym.Instrument{}}
	insertScope(t, ctx, db, 1, 8)
	enqueueSymbol(t, ctx, db)
	runSymbol(t, ctx, db, cat)
	if status, class := waitSymbol(t, ctx, db, 1); status != "succeeded" || class != "" {
		t.Fatalf("missing %s %s", status, class)
	}
	if len(symbols(t, ctx, db)) != 0 {
		t.Fatal("not found wrote a symbol")
	}
	attempts, result := lookupOf(t, ctx, db, 8)
	if attempts != 1 || result != "not_found" {
		t.Fatalf("lookup %d %s", attempts, result)
	}
	if n := unresolvedScopeUIDs(t, ctx, db); n != 1 {
		t.Fatalf("unresolved %d", n)
	}

	cat.result[8] = sym.Instrument{
		UID: 8, Venue: "BinanceUM", Category: "Perp", ExchTicker: "ETHUSDT",
		UniversalTicker: "BinanceUM_Perp_ETHUSDT", Base: "ETH", Quote: "USDT", Status: "TRADING",
	}
	enqueueSymbol(t, ctx, db)
	if status, class := waitSymbol(t, ctx, db, 2); status != "succeeded" || class != "" {
		t.Fatalf("found %s %s", status, class)
	}
	row := symbols(t, ctx, db)[8]
	if !row.Active || row.Ticker != "ETHUSDT" || row.Venue != "BinanceUM" || row.Category != "Perp" {
		t.Fatalf("%+v", row)
	}
	if n := lookupCount(t, ctx, db); n != 0 {
		t.Fatalf("lookup rows %d", n)
	}
	if n := unresolvedScopeUIDs(t, ctx, db); n != 0 {
		t.Fatalf("unresolved %d", n)
	}
}

func TestScopeUIDUnparsable(t *testing.T) {
	ctx, db := testDB(t)
	cat := &scriptUID{result: map[uint32]sym.Instrument{
		4: {UID: 4, ExchTicker: "NOPE", UniversalTicker: "not a ticker", Base: "N", Quote: "O", Status: "TRADING"},
	}}
	insertScope(t, ctx, db, 1, 4)
	enqueueSymbol(t, ctx, db)
	runSymbol(t, ctx, db, cat)
	if status, class := waitSymbol(t, ctx, db, 1); status != "succeeded" || class != "" {
		t.Fatalf("%s %s", status, class)
	}
	if len(symbols(t, ctx, db)) != 0 {
		t.Fatal("unparsable wrote a symbol")
	}
	attempts, result := lookupOf(t, ctx, db, 4)
	if attempts != 1 || result != "unparsable" {
		t.Fatalf("lookup %d %s", attempts, result)
	}
}

func TestNewScopeUIDIsNotStarved(t *testing.T) {
	ctx, db := testDB(t)
	if _, err := db.Exec(ctx, `
		INSERT INTO mirror.scope (api_id, uid, since, first_seen_at, last_seen_at)
		SELECT 1, g, now(), now(), now() FROM generate_series(1, $1) g`, maxScopeUIDs); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(ctx, `
		INSERT INTO mirror.uid_lookup (uid, first_tried_at, last_tried_at, attempts, last_result)
		SELECT g, now(), now(), 4, 'not_found' FROM generate_series(1, $1) g`, maxScopeUIDs); err != nil {
		t.Fatal(err)
	}
	const fresh int64 = 100000
	insertScope(t, ctx, db, 1, fresh)
	cat := &scriptUID{result: map[uint32]sym.Instrument{
		uint32(fresh): {
			UID: uint32(fresh), Venue: "BinanceUM", Category: "Perp", ExchTicker: "NEWUSDT",
			UniversalTicker: "BinanceUM_Perp_NEWUSDT", Base: "NEW", Quote: "USDT", Status: "TRADING",
		},
	}}
	enqueueSymbol(t, ctx, db)
	runSymbol(t, ctx, db, cat)
	if status, class := waitSymbol(t, ctx, db, 1); status != "succeeded" || class != "" {
		t.Fatalf("%s %s", status, class)
	}
	if len(cat.calls) != maxScopeUIDs {
		t.Fatalf("calls %d", len(cat.calls))
	}
	if cat.calls[0] != uint32(fresh) {
		t.Fatalf("first call %d", cat.calls[0])
	}
	row, ok := symbols(t, ctx, db)[fresh]
	if !ok || row.Ticker != "NEWUSDT" || !row.Active {
		t.Fatalf("%+v ok %v", row, ok)
	}
}

func TestScopeUIDOtherErrorFailsTheJob(t *testing.T) {
	ctx, db := testDB(t)
	cat := &scriptUID{fail: &classify.ClassifiedError{Class: classify.Unavailable, Detail: "sym down"}}
	insertScope(t, ctx, db, 1, 3)
	enqueueSymbol(t, ctx, db)
	runSymbol(t, ctx, db, cat)
	if status, class := waitSymbol(t, ctx, db, 1); status != "failed" || class != "unavailable" {
		t.Fatalf("%s %s", status, class)
	}
	if n := lookupCount(t, ctx, db); n != 0 {
		t.Fatalf("lookup rows %d", n)
	}
}

type scriptUID struct {
	result map[uint32]sym.Instrument
	fail   error
	calls  []uint32
}

func (s *scriptUID) Revisions(context.Context) ([]sym.Table, error) { return nil, nil }

func (s *scriptUID) List(context.Context, string, string, uint32) (sym.Page, error) {
	return sym.Page{}, nil
}

func (s *scriptUID) GetByUid(_ context.Context, uid uint32, includeInactive bool) (sym.Instrument, error) {
	if !includeInactive {
		return sym.Instrument{}, &classify.ClassifiedError{Class: classify.Internal, Detail: "include_inactive is false"}
	}
	s.calls = append(s.calls, uid)
	if s.fail != nil {
		return sym.Instrument{}, s.fail
	}
	inst, ok := s.result[uid]
	if !ok {
		return sym.Instrument{}, &classify.ClassifiedError{Class: classify.NotFound, Detail: "missing"}
	}
	return inst, nil
}

func insertScope(t *testing.T, ctx context.Context, db *pgxpool.Pool, apiID, uid int64) {
	t.Helper()
	if _, err := db.Exec(ctx, `
		INSERT INTO mirror.scope (api_id, uid, since, first_seen_at, last_seen_at)
		VALUES ($1, $2, $3, now(), now())`, apiID, uid, time.Date(2026, 11, 7, 0, 0, 0, 0, time.UTC)); err != nil {
		t.Fatal(err)
	}
}

func lookupCount(t *testing.T, ctx context.Context, db *pgxpool.Pool) int {
	t.Helper()
	var n int
	if err := db.QueryRow(ctx, `SELECT count(*) FROM mirror.uid_lookup`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

func lookupOf(t *testing.T, ctx context.Context, db *pgxpool.Pool, uid int64) (int, string) {
	t.Helper()
	var attempts int
	var result string
	if err := db.QueryRow(ctx, `SELECT attempts, last_result FROM mirror.uid_lookup WHERE uid = $1`, uid).Scan(&attempts, &result); err != nil {
		t.Fatal(err)
	}
	return attempts, result
}

func unresolvedScopeUIDs(t *testing.T, ctx context.Context, db *pgxpool.Pool) int {
	t.Helper()
	var n int
	if err := db.QueryRow(ctx, `
		SELECT count(DISTINCT s.uid)
		FROM mirror.scope s
		LEFT JOIN mirror.symbol y ON y.uid = s.uid
		WHERE y.uid IS NULL`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}
