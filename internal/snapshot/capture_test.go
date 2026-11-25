package snapshot

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BullionBear/cpanel/internal/platform/pgtest"
	"github.com/BullionBear/cpanel/internal/reconcile"
	"github.com/BullionBear/cpanel/migrations"
)

var hour = time.Date(2026, 11, 7, 12, 0, 0, 0, time.UTC)

func testPool(t *testing.T) (context.Context, *pgxpool.Pool) {
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

func exec(t *testing.T, ctx context.Context, db *pgxpool.Pool, sql string, args ...any) {
	t.Helper()
	if _, err := db.Exec(ctx, sql, args...); err != nil {
		t.Fatalf("%s: %v", sql, err)
	}
}

func addAccount(t *testing.T, ctx context.Context, db *pgxpool.Pool, kinds []string, verified time.Time) int64 {
	t.Helper()
	var id int64
	if err := db.QueryRow(ctx, `
		INSERT INTO pms.exchange_account (exchange, exch_account_uid, label)
		VALUES ('binance', 'uid', 'desk') RETURNING id`).Scan(&id); err != nil {
		t.Fatal(err)
	}
	for _, kind := range kinds {
		exec(t, ctx, db, `
			INSERT INTO pms.exchange_account_kind (exchange_account_id, kind) VALUES ($1, $2)`, id, kind)
	}
	exec(t, ctx, db, `
		INSERT INTO pms.credential (
			exchange_account_id, purpose, status, ciphertext, api_key_last4, verified_at)
		VALUES ($1, 'read', 'active', '\x01', 'abcd', $2)`, id, verified)
	return id
}

func runRule(t *testing.T, ctx context.Context, db *pgxpool.Pool, now time.Time) int {
	t.Helper()
	var n int
	err := pgx.BeginFunc(ctx, db, func(tx pgx.Tx) error {
		var err error
		n, err = reconcile.Snapshots([]string{"BinanceUM"}).Run(ctx, tx, now)
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	return n
}

func TestPositionLookupAndBalance(t *testing.T) {
	ctx, db := testPool(t)
	id := addAccount(t, ctx, db, []string{"BinanceUM"}, hour)
	var batch int64
	if err := db.QueryRow(ctx, `
		INSERT INTO ledger.snapshot_batch (exchange_account_id, bucket, captured_at)
		VALUES ($1, $2, $2) RETURNING id`, id, hour).Scan(&batch); err != nil {
		t.Fatal(err)
	}
	exec(t, ctx, db, `
		INSERT INTO mirror.symbol (
			uid, venue, category, exch_ticker, universal_ticker, base, quote, status, active, first_seen_at, last_seen_at)
		VALUES (42, 'BinanceUM', 'Perp', 'BTCUSDT', 'BinanceUM_Perp_BTCUSDT', 'BTC', 'USDT', 'TRADING', true, $1, $1)`, hour)

	err := pgx.BeginFunc(ctx, db, func(tx pgx.Tx) error {
		if err := AddBalance(ctx, tx, batch, Balance{
			Kind: "BinanceUM", Wallet: WalletUM, Asset: "USDT",
			Balance: "1.5", AvailableBalance: "1", CrossWalletBalance: "1.25", MaxWithdraw: "0.5",
		}); err != nil {
			return err
		}
		if err := AddPosition(ctx, tx, batch, Position{
			Kind: "BinanceUM", Venue: "BinanceUM", ExchTicker: "BTCUSDT", PositionSide: "BOTH",
			PositionAmt: "-1.5", EntryPrice: "10", MarkPrice: "11", UnrealizedProfit: "-0.1",
			IsolatedMargin: "2", InitialMargin: "3", PositionInitialMargin: "4", IsolatedWallet: "5",
		}); err != nil {
			return err
		}
		return AddPosition(ctx, tx, batch, Position{
			Kind: "BinanceUM", Venue: "BinanceUM", ExchTicker: "ETHUSDT", PositionSide: "SHORT",
			PositionAmt: "2", EntryPrice: "1", MarkPrice: "1", UnrealizedProfit: "0",
			IsolatedMargin: "0", InitialMargin: "0", PositionInitialMargin: "0", IsolatedWallet: "0",
		})
	})
	if err != nil {
		t.Fatal(err)
	}
	var wallet, balance string
	var version int
	if err := db.QueryRow(ctx, `
		SELECT wallet, balance::text, norm_version FROM ledger.balance_snapshot WHERE batch_id = $1`, batch).
		Scan(&wallet, &balance, &version); err != nil {
		t.Fatal(err)
	}
	if wallet != WalletUM || balance != "1.5" || version != NormVersion {
		t.Fatalf("balance %s %s version %d", wallet, balance, version)
	}
	var uid *int64
	var category *string
	var ticker, amt string
	if err := db.QueryRow(ctx, `
		SELECT category, uid, exch_ticker, position_amt::text FROM ledger.position_snapshot
		WHERE batch_id = $1 AND exch_ticker = 'BTCUSDT'`, batch).Scan(&category, &uid, &ticker, &amt); err != nil {
		t.Fatal(err)
	}
	if category == nil || *category != "Perp" || uid == nil || *uid != 42 || amt != "-1.5" {
		t.Fatalf("btc category %v uid %v amt %s", category, uid, amt)
	}
	if err := db.QueryRow(ctx, `
		SELECT category, uid, exch_ticker FROM ledger.position_snapshot
		WHERE batch_id = $1 AND exch_ticker = 'ETHUSDT'`, batch).Scan(&category, &uid, &ticker); err != nil {
		t.Fatal(err)
	}
	if category != nil || uid != nil || ticker != "ETHUSDT" {
		t.Fatalf("missing symbol stored category %v uid %v ticker %s", category, uid, ticker)
	}

	err = pgx.BeginFunc(ctx, db, func(tx pgx.Tx) error {
		return AddPosition(ctx, tx, batch, Position{
			Kind: "BinanceUM", Venue: "BinanceUM", ExchTicker: "BTCUSDT", PositionSide: "LONG",
			PositionAmt: "0", EntryPrice: "1", MarkPrice: "1", UnrealizedProfit: "0",
			IsolatedMargin: "0", InitialMargin: "0", PositionInitialMargin: "0", IsolatedWallet: "0",
		})
	})
	var pgErr *pgconn.PgError
	if !errors.As(err, &pgErr) || pgErr.Code != "23514" {
		t.Fatalf("zero position_amt err = %v, want check_violation", err)
	}
	_, err = db.Exec(ctx, `
		INSERT INTO ledger.position_snapshot (
			batch_id, kind, venue, category, exch_ticker, position_side, position_amt, entry_price, mark_price,
			unrealized_profit, isolated_margin, initial_margin, position_initial_margin, isolated_wallet)
		VALUES ($1, 'BinanceUM', 'BinanceUM', '', 'XRPUSDT', 'BOTH', 1, 0, 0, 0, 0, 0, 0, 0)`, batch)
	if !errors.As(err, &pgErr) || pgErr.Code != "23514" {
		t.Fatalf("empty category err = %v, want check_violation", err)
	}
}

func TestVenueSnapshotPartitionIsImmutable(t *testing.T) {
	ctx, db := testPool(t)
	id := addAccount(t, ctx, db, []string{"BinanceUM"}, hour)
	fetched := time.Date(2026, 11, 23, 6, 5, 0, 0, time.UTC)
	var rawID int64
	if err := db.QueryRow(ctx, `
		INSERT INTO raw.venue_snapshot (
			exchange_account_id, venue, kind, bucket, params, payload, fetched_at)
		VALUES ($1, 'BinanceUM', 'BinanceUM', $2, '{"path":"/fapi/v3/balance"}', '{"rows":[]}', $3)
		RETURNING id`, id, hour, fetched).Scan(&rawID); err != nil {
		t.Fatal(err)
	}
	var made bool
	if err := db.QueryRow(ctx, `SELECT raw.ensure_venue_snapshot_partition($1::date)`, time.Date(2026, 11, 2, 0, 0, 0, 0, time.UTC)).Scan(&made); err != nil {
		t.Fatal(err)
	}
	if !made {
		t.Fatal("first ensure made nothing")
	}
	if err := db.QueryRow(ctx, `SELECT raw.ensure_venue_snapshot_partition('2026-11-01')`).Scan(&made); err != nil {
		t.Fatal(err)
	}
	if made {
		t.Fatal("second ensure made another partition")
	}
	var part string
	if err := db.QueryRow(ctx, `
		SELECT c.relname FROM pg_class c
		WHERE c.oid = (SELECT tableoid FROM raw.venue_snapshot WHERE id = $1)`, rawID).Scan(&part); err != nil {
		t.Fatal(err)
	}
	if part != "venue_snapshot_202611" {
		t.Fatalf("row is in %s", part)
	}
	_, err := db.Exec(ctx, `UPDATE raw.venue_snapshot SET payload = '{}' WHERE id = $1`, rawID)
	var pgErr *pgconn.PgError
	if !errors.As(err, &pgErr) || pgErr.Message != "raw.venue_snapshot is immutable" {
		t.Fatalf("update err = %v", err)
	}
	_, err = db.Exec(ctx, `DELETE FROM raw.venue_snapshot WHERE id = $1`, rawID)
	if !errors.As(err, &pgErr) || pgErr.Message != "raw.venue_snapshot is immutable" {
		t.Fatalf("delete err = %v", err)
	}
}

func gaps(t *testing.T, ctx context.Context, db *pgxpool.Pool, asOf time.Time) []time.Time {
	t.Helper()
	rows, err := db.Query(ctx, `
		SELECT bucket FROM ledger.snapshot_gaps($1::text[], $2) ORDER BY bucket`, []string{"BinanceUM"}, asOf)
	if err != nil {
		t.Fatal(err)
	}
	out, err := pgx.CollectRows(rows, pgx.RowTo[time.Time])
	if err != nil {
		t.Fatal(err)
	}
	return out
}
