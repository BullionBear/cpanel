package reconcile

import (
	"context"
	"fmt"
	"sync"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

func insertAccount(t *testing.T, ctx context.Context, conn *pgx.Conn, exchange, exchUID string, kinds ...string) int64 {
	t.Helper()
	var id int64
	if err := conn.QueryRow(ctx, `
		INSERT INTO pms.exchange_account (exchange, exch_account_uid, label)
		VALUES ($1, $2, $2) RETURNING id`, exchange, exchUID).Scan(&id); err != nil {
		t.Fatal(err)
	}
	for _, kind := range kinds {
		exec(t, ctx, conn, `
			INSERT INTO pms.exchange_account_kind (exchange_account_id, kind) VALUES ($1, $2)`, id, kind)
	}
	return id
}

func insertMirror(t *testing.T, ctx context.Context, conn *pgx.Conn, apiID int64, kind, exchange, exchUID string) {
	t.Helper()
	exec(t, ctx, conn, `
		INSERT INTO mirror.account (api_id, name, kind, exchange, exch_account_uid, account_revision, first_seen_at, last_seen_at)
		VALUES ($1, $2, $2, $3, $4, 1, $5, $5)`, apiID, kind, exchange, exchUID, t0)
}

func insertSymbol(t *testing.T, ctx context.Context, conn *pgx.Conn, uid int64, venue, category, ticker string) {
	t.Helper()
	exec(t, ctx, conn, `
		INSERT INTO mirror.symbol (
			uid, venue, category, exch_ticker, universal_ticker, base, quote, status, active, first_seen_at, last_seen_at)
		VALUES ($1, $2, $3, $4, $4, 'B', 'Q', 'Trading', true, $5, $5)`, uid, venue, category, ticker, t0)
}

func insertScope(t *testing.T, ctx context.Context, conn *pgx.Conn, apiID, uid int64, since time.Time) {
	t.Helper()
	exec(t, ctx, conn, `
		INSERT INTO mirror.scope (api_id, uid, since, first_seen_at, last_seen_at)
		VALUES ($1, $2, $3, $4, $4)`, apiID, uid, since, t0)
}

type listed struct {
	Account          int64
	Venue, Source    string
	UID              int64
	Since, FirstSeen time.Time
}

func lists(t *testing.T, ctx context.Context, conn *pgx.Conn) []listed {
	t.Helper()
	rows, err := conn.Query(ctx, `
		SELECT exchange_account_id, venue, uid, source, since, first_seen_at
		FROM ingest.persist_symbol
		ORDER BY exchange_account_id, venue, uid, source`)
	if err != nil {
		t.Fatal(err)
	}
	out, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (listed, error) {
		var l listed
		err := row.Scan(&l.Account, &l.Venue, &l.UID, &l.Source, &l.Since, &l.FirstSeen)
		l.Since, l.FirstSeen = l.Since.UTC(), l.FirstSeen.UTC()
		return l, err
	})
	if err != nil {
		t.Fatal(err)
	}
	return out
}

type planned struct {
	Kind, Lease, Key, Source, Tier string
	Period                         time.Duration
	Due                            time.Time
	Cursor                         *string
}

func plannedStreams(t *testing.T, ctx context.Context, conn *pgx.Conn) []planned {
	t.Helper()
	rows, err := conn.Query(ctx, `
		SELECT kind, lease_key, key, source, tier, period, next_due_at, cursor::text
		FROM ingest.stream
		WHERE kind IN ('trade.history', 'funding.history')
		ORDER BY kind, lease_key, key`)
	if err != nil {
		t.Fatal(err)
	}
	out, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (planned, error) {
		var p planned
		var period time.Duration
		err := row.Scan(&p.Kind, &p.Lease, &p.Key, &p.Source, &p.Tier, &period, &p.Due, &p.Cursor)
		p.Period, p.Due = period, p.Due.UTC()
		return p, err
	})
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func TestPersistProjectsTheEarlierSince(t *testing.T) {
	ctx, _, conn := testDB(t)
	account := insertAccount(t, ctx, conn, "binance", "acc-1", "Binance", "BinanceUM")
	insertMirror(t, ctx, conn, 10, "Binance", "binance", "acc-1")
	insertMirror(t, ctx, conn, 11, "BinanceUM", "binance", "acc-1")
	insertSymbol(t, ctx, conn, 42, "BinanceUM", "Perp", "BTCUSDT")
	insertSymbol(t, ctx, conn, 43, "Binance", "Spot", "BTCUSDT")
	early, late := t0, t0.Add(time.Hour)
	insertScope(t, ctx, conn, 10, 42, early)
	insertScope(t, ctx, conn, 11, 42, late)
	insertScope(t, ctx, conn, 10, 43, early)

	if n := run(t, ctx, conn, Persist, t0); n != 5 {
		t.Fatalf("inserted %d, want 2 list rows and 3 streams", n)
	}
	got := lists(t, ctx, conn)
	wantSince := early.Add(-time.Minute)
	um, spot := listSource(t, got, 42, "mftiko"), listSource(t, got, 43, "mftiko")
	if len(got) != 2 || um.Venue != "BinanceUM" || spot.Venue != "Binance" ||
		!um.Since.Equal(wantSince) || !spot.Since.Equal(wantSince) || !um.FirstSeen.Equal(t0) {
		t.Fatalf("list %+v", got)
	}
	streams := plannedStreams(t, ctx, conn)
	if len(streams) != 3 || streams[0].Kind != "funding.history" || streams[0].Key != "BinanceUM" ||
		streams[0].Source != "system" || streams[0].Period != time.Hour || streams[0].Cursor != nil ||
		streams[1].Key != "Binance:43" || streams[1].Period != 24*time.Hour || streams[1].Tier != "cold" ||
		streams[2].Key != "BinanceUM:42" || streams[2].Source != "mftiko" || streams[2].Tier != "cold" {
		t.Fatalf("streams %+v", streams)
	}
	if n := run(t, ctx, conn, Persist, t0); n != 0 {
		t.Fatalf("second round wrote %d rows", n)
	}

	exec(t, ctx, conn, `UPDATE mirror.scope SET since = $1 WHERE api_id = 10 AND uid = 42`, early.Add(-2*time.Hour))
	if n := run(t, ctx, conn, Persist, t0.Add(time.Minute)); n != 1 {
		t.Fatalf("lowered since wrote %d rows, want the list row only", n)
	}
	lowered := early.Add(-2*time.Hour - time.Minute)
	if got := listSource(t, lists(t, ctx, conn), 42, "mftiko").Since; !got.Equal(lowered) {
		t.Fatalf("since %s", got)
	}
	if got := listSource(t, lists(t, ctx, conn), 42, "mftiko").FirstSeen; !got.Equal(t0) {
		t.Fatalf("first_seen_at moved to %s", got)
	}

	exec(t, ctx, conn, `UPDATE mirror.scope SET since = $1 WHERE api_id = 10 AND uid = 42`, early.Add(5*time.Hour))
	run(t, ctx, conn, Persist, t0)
	if got := listSource(t, lists(t, ctx, conn), 42, "mftiko").Since; !got.Equal(lowered) {
		t.Fatalf("since grew to %s", got)
	}

	exec(t, ctx, conn, `DELETE FROM mirror.scope WHERE uid = 42`)
	run(t, ctx, conn, Persist, t0)
	if got := listSource(t, lists(t, ctx, conn), 42, "mftiko").Since; !got.Equal(lowered) || len(lists(t, ctx, conn)) != 2 {
		t.Fatalf("list after scope disappeared: %+v", lists(t, ctx, conn))
	}

	manualSince := early.Add(-3 * time.Hour)
	exec(t, ctx, conn, `
		INSERT INTO ingest.persist_symbol (exchange_account_id, venue, uid, source, since, first_seen_at)
		VALUES ($1, 'BinanceUM', 42, 'manual', $2, $3)`, account, manualSince, t0)
	run(t, ctx, conn, Persist, t0)
	found := lists(t, ctx, conn)
	if len(found) != 3 || !listSource(t, found, 42, "manual").Since.Equal(manualSince) {
		t.Fatalf("manual row %+v", found)
	}
	streams = plannedStreams(t, ctx, conn)
	if streams[2].Key != "BinanceUM:42" || streams[2].Source != "manual" {
		t.Fatalf("stream source %+v, want the earlier manual row", streams[2])
	}
}

func listSource(t *testing.T, rows []listed, uid int64, source string) listed {
	t.Helper()
	for _, row := range rows {
		if row.UID == uid && row.Source == source {
			return row
		}
	}
	t.Fatalf("no list row uid %d source %s in %+v", uid, source, rows)
	return listed{}
}

func TestPersistSkipsUnusableScopes(t *testing.T) {
	ctx, _, conn := testDB(t)
	insertAccount(t, ctx, conn, "binance", "spot-only", "Binance")
	insertMirror(t, ctx, conn, 20, "Binance", "binance", "spot-only")
	insertSymbol(t, ctx, conn, 42, "BinanceUM", "Perp", "BTCUSDT")
	insertScope(t, ctx, conn, 20, 42, t0)

	insertAccount(t, ctx, conn, "binance", "no-kind")
	insertMirror(t, ctx, conn, 21, "BinanceUM", "binance", "no-kind")
	insertScope(t, ctx, conn, 21, 42, t0)

	disabled := insertAccount(t, ctx, conn, "binance", "disabled", "BinanceUM")
	exec(t, ctx, conn, `UPDATE pms.exchange_account_kind SET disabled_at = $1 WHERE exchange_account_id = $2`, t0, disabled)
	insertMirror(t, ctx, conn, 22, "BinanceUM", "binance", "disabled")
	insertScope(t, ctx, conn, 22, 42, t0)

	insertMirror(t, ctx, conn, 23, "BinanceUM", "binance", "missing-account")
	insertScope(t, ctx, conn, 23, 42, t0)

	insertAccount(t, ctx, conn, "binance", "unknown-uid", "BinanceUM")
	insertMirror(t, ctx, conn, 24, "BinanceUM", "binance", "unknown-uid")
	insertScope(t, ctx, conn, 24, 404, t0)

	insertAccount(t, ctx, conn, "binance", "coin-m", "Binance")
	insertMirror(t, ctx, conn, 25, "BinanceCM", "binance", "coin-m")
	insertSymbol(t, ctx, conn, 77, "BinanceCM", "Perp", "BTCUSD")
	insertScope(t, ctx, conn, 25, 77, t0)

	if n := run(t, ctx, conn, Persist, t0); n != 0 {
		t.Fatalf("wrote %d rows from scopes that should not list", n)
	}
	if got := lists(t, ctx, conn); len(got) != 0 {
		t.Fatalf("list %+v", got)
	}
}

func TestPersistTierFollowsTradesAndTheLatestPosition(t *testing.T) {
	ctx, _, conn := testDB(t)
	account := insertAccount(t, ctx, conn, "binance", "acc-1", "BinanceUM")
	insertMirror(t, ctx, conn, 11, "BinanceUM", "binance", "acc-1")
	insertSymbol(t, ctx, conn, 42, "BinanceUM", "Perp", "BTCUSDT")
	insertScope(t, ctx, conn, 11, 42, t0)
	run(t, ctx, conn, Persist, t0)
	exec(t, ctx, conn, `UPDATE ingest.stream SET next_due_at = $1 WHERE kind = 'trade.history'`, t0.Add(48*time.Hour))

	exec(t, ctx, conn, `
		INSERT INTO raw.venue_trade_key (exchange_account_id, venue, native_id) VALUES ($1, 'BinanceUM', 'old')`, account)
	exec(t, ctx, conn, `
		INSERT INTO ledger.trade (
			exchange_account_id, venue, native_id, uid, order_native_id, price, qty, quote_qty,
			side, position_side, traded_at, commission, commission_asset, realized_pnl, norm_version)
		VALUES ($1, 'BinanceUM', 'old', 42, 'o', 1, 1, 1, 'BUY', 'BOTH', $2, 0, 'USDT', 0, 1)`,
		account, t0.Add(-24*time.Hour-time.Second))
	run(t, ctx, conn, Persist, t0)
	if got := plannedStreams(t, ctx, conn); got[1].Tier != "cold" || got[1].Due.Equal(t0) {
		t.Fatalf("old trade made %+v", got[1])
	}

	exec(t, ctx, conn, `
		INSERT INTO raw.venue_trade_key (exchange_account_id, venue, native_id) VALUES ($1, 'BinanceUM', 'new')`, account)
	exec(t, ctx, conn, `
		INSERT INTO ledger.trade (
			exchange_account_id, venue, native_id, uid, order_native_id, price, qty, quote_qty,
			side, position_side, traded_at, commission, commission_asset, realized_pnl, norm_version)
		VALUES ($1, 'BinanceUM', 'new', 42, 'o', 1, 1, 1, 'BUY', 'BOTH', $2, 0, 'USDT', 0, 1)`,
		account, t0.Add(-time.Hour))
	run(t, ctx, conn, Persist, t0)
	if got := plannedStreams(t, ctx, conn); got[1].Tier != "hot" || got[1].Period != 2*time.Minute || !got[1].Due.Equal(t0) {
		t.Fatalf("recent trade made %+v", got[1])
	}
	if got := plannedStreams(t, ctx, conn); got[0].Kind != "funding.history" || got[0].Period != time.Hour {
		t.Fatalf("funding %+v", got[0])
	}

	bybit := insertAccount(t, ctx, conn, "bybit", "by-1", "Bybit")
	insertMirror(t, ctx, conn, 30, "Bybit", "bybit", "by-1")
	insertSymbol(t, ctx, conn, 7, "Bybit", "linear", "BTCUSDT")
	insertSymbol(t, ctx, conn, 8, "Bybit", "linear", "ETHUSDT")
	insertScope(t, ctx, conn, 30, 7, t0)
	insertScope(t, ctx, conn, 30, 8, t0)
	var batch int64
	if err := conn.QueryRow(ctx, `
		INSERT INTO ledger.snapshot_batch (exchange_account_id, bucket, captured_at)
		VALUES ($1, $2, $2) RETURNING id`, bybit, t0).Scan(&batch); err != nil {
		t.Fatal(err)
	}
	exec(t, ctx, conn, `
		INSERT INTO ledger.position_snapshot (
			batch_id, kind, venue, category, exch_ticker, uid, position_side, position_amt,
			entry_price, mark_price, unrealized_profit, isolated_margin, initial_margin,
			position_initial_margin, isolated_wallet)
		VALUES ($1, 'Bybit', 'Bybit', 'linear', 'BTCUSDT', 7, 'BOTH', 1, 0, 0, 0, 0, 0, 0, 0)`, batch)
	if err := conn.QueryRow(ctx, `
		INSERT INTO ledger.snapshot_batch (exchange_account_id, bucket, captured_at)
		VALUES ($1, $2, $2) RETURNING id`, bybit, t0.Add(time.Hour)).Scan(&batch); err != nil {
		t.Fatal(err)
	}
	run(t, ctx, conn, Persist, t0.Add(time.Hour))
	var tier string
	var period time.Duration
	if err := conn.QueryRow(ctx, `
		SELECT tier, period FROM ingest.stream
		WHERE kind = 'trade.history' AND lease_key = $1`, fmt.Sprintf("account:%d", bybit)).Scan(&tier, &period); err != nil {
		t.Fatal(err)
	}
	if tier != "cold" || period != 24*time.Hour {
		t.Fatalf("latest empty snapshot left tier %s period %s", tier, period)
	}
	exec(t, ctx, conn, `
		INSERT INTO ledger.position_snapshot (
			batch_id, kind, venue, category, exch_ticker, uid, position_side, position_amt,
			entry_price, mark_price, unrealized_profit, isolated_margin, initial_margin,
			position_initial_margin, isolated_wallet)
		VALUES ($1, 'Bybit', 'Bybit', 'linear', 'ETHUSDT', 8, 'BOTH', -2, 0, 0, 0, 0, 0, 0, 0)`, batch)
	run(t, ctx, conn, Persist, t0.Add(time.Hour))
	var n int
	if err := conn.QueryRow(ctx, `
		SELECT count(*) FROM ingest.stream
		WHERE lease_key = $1 AND kind = 'trade.history' AND key = 'Bybit:linear'
		  AND tier = 'hot' AND period = interval '2 minutes'`, fmt.Sprintf("account:%d", bybit)).Scan(&n); err != nil {
		t.Fatal(err)
	}
	if n != 1 {
		t.Fatalf("bybit trade streams hot = %d, want one category stream", n)
	}
}

func TestThreeLoopsPlanTheSameRows(t *testing.T) {
	one := planOnce(t, 1)
	three := planOnce(t, 3)
	if one != three {
		t.Fatalf("one loop planned %q, three planned %q", one, three)
	}
	if one == "" {
		t.Fatal("planned nothing")
	}
}

func planOnce(t *testing.T, loops int) string {
	t.Helper()
	ctx, dsn, conn := testDB(t)
	insertAccount(t, ctx, conn, "binance", "acc-1", "Binance", "BinanceUM")
	insertMirror(t, ctx, conn, 10, "Binance", "binance", "acc-1")
	insertMirror(t, ctx, conn, 11, "BinanceUM", "binance", "acc-1")
	insertSymbol(t, ctx, conn, 42, "BinanceUM", "Perp", "BTCUSDT")
	insertScope(t, ctx, conn, 10, 42, t0)
	insertScope(t, ctx, conn, 11, 42, t0.Add(time.Hour))

	ls := make([]*Loop, loops)
	for i := range ls {
		pool, err := pgxpool.New(ctx, dsn)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(pool.Close)
		ls[i] = &Loop{DB: pool, Rules: Rules(nil), Now: func() time.Time { return t0 }}
	}
	start := make(chan struct{})
	results := make([]Result, loops)
	var wg sync.WaitGroup
	for i, l := range ls {
		wg.Go(func() {
			<-start
			results[i] = l.Round(ctx)
		})
	}
	close(start)
	wg.Wait()
	for i, res := range results {
		if res.Err != nil {
			t.Fatalf("loop %d: %v", i, res.Err)
		}
	}
	var text string
	if err := conn.QueryRow(ctx, `
		SELECT coalesce(string_agg(row, E'\n' ORDER BY row), '') FROM (
			SELECT exchange_account_id::text || ' ' || venue || ' ' || uid::text || ' ' || source || ' ' || since::text
			       AS row
			FROM ingest.persist_symbol
			UNION ALL
			SELECT kind || ' ' || lease_key || ' ' || key || ' ' || source || ' ' || tier || ' ' || period::text
			FROM ingest.stream
			WHERE kind IN ('trade.history', 'funding.history')
		) s`).Scan(&text); err != nil {
		t.Fatal(err)
	}
	return text
}
