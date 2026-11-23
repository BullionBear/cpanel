package history

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BullionBear/cpanel/internal/platform/pgtest"
	"github.com/BullionBear/cpanel/migrations"
)

var fetched = time.Date(2026, 11, 7, 12, 5, 0, 0, time.UTC)

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

func addAccount(t *testing.T, ctx context.Context, db *pgxpool.Pool) int64 {
	t.Helper()
	var id int64
	if err := db.QueryRow(ctx, `
		INSERT INTO pms.exchange_account (exchange, exch_account_uid, label)
		VALUES ('binance', 'uid', 'desk') RETURNING id`).Scan(&id); err != nil {
		t.Fatal(err)
	}
	return id
}

type fake struct {
	venue   string
	kind    Kind
	version int
}

func (f fake) Venue() string { return f.venue }
func (f fake) Kind() Kind    { return f.kind }
func (f fake) Version() int  { return f.version }

func (f fake) Normalize(_ context.Context, _ pgx.Tx, raw Raw) (Result, error) {
	switch f.kind {
	case KindTrade:
		var body Trade
		if err := json.Unmarshal(raw.Payload, &body); err != nil {
			return Result{}, err
		}
		return Result{Trade: &body}, nil
	case KindOrder:
		var body Order
		if err := json.Unmarshal(raw.Payload, &body); err != nil {
			return Result{}, err
		}
		return Result{Order: &body}, nil
	case KindFunding:
		var body Funding
		if err := json.Unmarshal(raw.Payload, &body); err != nil {
			return Result{}, err
		}
		return Result{Funding: &body}, nil
	default:
		return Result{}, errors.New("kind")
	}
}

func TestRebuildMatchesAndBumpsVersion(t *testing.T) {
	ctx, db := testPool(t)
	id := addAccount(t, ctx, db)
	if _, err := db.Exec(ctx, `
		INSERT INTO ingest.stream (kind, lease_key, key, source, period, cursor)
		VALUES ('trade', 'account:1', 's', 'system', '1 hour', '{"n":1}')`); err != nil {
		t.Fatal(err)
	}
	traded := time.Date(2026, 11, 7, 12, 1, 0, 0, time.UTC)
	payload := func(native string) json.RawMessage {
		b, err := json.Marshal(Trade{
			UID: 7, OrderNativeID: "BTCUSDT:9", Price: "10", Qty: "2", QuoteQty: "20",
			Side: "BUY", PositionSide: "BOTH", TradedAt: traded,
			Commission: "0.1", CommissionAsset: "USDT", RealizedPnl: "0",
		})
		if err != nil {
			t.Fatal(err)
		}
		return b
	}
	n := fake{venue: "BinanceUM", kind: KindTrade, version: 1}
	err := pgx.BeginFunc(ctx, db, func(tx pgx.Tx) error {
		for _, native := range []string{"BTCUSDT:1", "BTCUSDT:2"} {
			if err := Apply(ctx, tx, n, Raw{
				ExchangeAccountID: id, Venue: "BinanceUM", NativeID: native,
				Params: json.RawMessage(`{"symbol":"BTCUSDT"}`), Payload: payload(native), FetchedAt: fetched,
			}); err != nil {
				return err
			}
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	before := tradeTexts(t, ctx, db, id)
	if len(before) != 2 || before[0].version != 1 {
		t.Fatalf("before %+v", before)
	}
	if _, err := db.Exec(ctx, `DELETE FROM ledger.trade WHERE exchange_account_id = $1`, id); err != nil {
		t.Fatal(err)
	}
	if err := Rebuild(ctx, db, n, id); err != nil {
		t.Fatal(err)
	}
	if got := tradeTexts(t, ctx, db, id); !sameTrades(before, got) {
		t.Fatalf("rebuild %+v, want %+v", got, before)
	}
	n.version = 2
	if err := Rebuild(ctx, db, n, id); err != nil {
		t.Fatal(err)
	}
	got := tradeTexts(t, ctx, db, id)
	if len(got) != 2 || got[0].version != 2 || got[1].version != 2 {
		t.Fatalf("version %+v", got)
	}
	got[0].version, got[1].version = 1, 1
	if !sameTrades(before, got) {
		t.Fatalf("columns changed at version 2: %+v vs %+v", got, before)
	}
	var cursor string
	if err := db.QueryRow(ctx, `SELECT cursor::text FROM ingest.stream`).Scan(&cursor); err != nil {
		t.Fatal(err)
	}
	if cursor != `{"n": 1}` && cursor != `{"n":1}` {
		t.Fatalf("cursor %s", cursor)
	}

	err = pgx.BeginFunc(ctx, db, func(tx pgx.Tx) error {
		return Apply(ctx, tx, n, Raw{
			ExchangeAccountID: id, Venue: "BinanceUM", NativeID: "BTCUSDT:1",
			Payload: payload("BTCUSDT:1"), FetchedAt: fetched,
		})
	})
	var pgErr *pgconn.PgError
	if !errors.As(err, &pgErr) || pgErr.Code != "23505" {
		t.Fatalf("duplicate raw err = %v, want unique_violation", err)
	}
}

func TestOrderAndFunding(t *testing.T) {
	ctx, db := testPool(t)
	id := addAccount(t, ctx, db)
	ver := 1
	algo := "twap"
	ts := int64(1_700_000_000_000)
	orderPayload, err := json.Marshal(Order{
		UID: 7, ClientOrderID: nil, CloidVer: &ver, Algo: &algo, CloidTsMs: &ts, ExecutedQty: "1.5",
	})
	if err != nil {
		t.Fatal(err)
	}
	fundPayload, err := json.Marshal(Funding{Asset: "USDT", Income: "-0.2", FundedAt: fetched})
	if err != nil {
		t.Fatal(err)
	}
	err = pgx.BeginFunc(ctx, db, func(tx pgx.Tx) error {
		if err := Apply(ctx, tx, fake{venue: "BinanceUM", kind: KindOrder, version: 1}, Raw{
			ExchangeAccountID: id, Venue: "BinanceUM", NativeID: "BTCUSDT:9",
			Payload: orderPayload, FetchedAt: fetched,
		}); err != nil {
			return err
		}
		return Apply(ctx, tx, fake{venue: "BinanceUM", kind: KindFunding, version: 1}, Raw{
			ExchangeAccountID: id, Venue: "BinanceUM", NativeID: "FUNDING_FEE:42",
			Payload: fundPayload, FetchedAt: fetched,
		})
	})
	if err != nil {
		t.Fatal(err)
	}
	var qty, algoGot string
	var cloidTs int64
	if err := db.QueryRow(ctx, `
		SELECT executed_qty::text, algo, cloid_ts_ms FROM ledger.order WHERE native_id = 'BTCUSDT:9'`).
		Scan(&qty, &algoGot, &cloidTs); err != nil {
		t.Fatal(err)
	}
	if qty != "1.5" || algoGot != "twap" || cloidTs != ts {
		t.Fatalf("order %s %s %d", qty, algoGot, cloidTs)
	}
	var uid *int64
	var income string
	if err := db.QueryRow(ctx, `
		SELECT uid, income::text FROM ledger.funding WHERE native_id = 'FUNDING_FEE:42'`).
		Scan(&uid, &income); err != nil {
		t.Fatal(err)
	}
	if uid != nil || income != "-0.2" {
		t.Fatalf("funding uid %v income %s", uid, income)
	}
	if _, err := db.Exec(ctx, `DELETE FROM ledger.order WHERE exchange_account_id = $1`, id); err != nil {
		t.Fatal(err)
	}
	if err := Rebuild(ctx, db, fake{venue: "BinanceUM", kind: KindOrder, version: 3}, id); err != nil {
		t.Fatal(err)
	}
	var version int
	if err := db.QueryRow(ctx, `SELECT norm_version FROM ledger.order`).Scan(&version); err != nil {
		t.Fatal(err)
	}
	if version != 3 {
		t.Fatalf("order version %d", version)
	}
}

func TestRawHistoryIsImmutable(t *testing.T) {
	ctx, db := testPool(t)
	id := addAccount(t, ctx, db)
	payload, err := json.Marshal(Trade{
		UID: 7, OrderNativeID: "BTCUSDT:9", Price: "1", Qty: "1", QuoteQty: "1",
		Side: "BUY", PositionSide: "BOTH", TradedAt: fetched,
		Commission: "0", CommissionAsset: "USDT", RealizedPnl: "0",
	})
	if err != nil {
		t.Fatal(err)
	}
	err = pgx.BeginFunc(ctx, db, func(tx pgx.Tx) error {
		return Apply(ctx, tx, fake{venue: "BinanceUM", kind: KindTrade, version: 1}, Raw{
			ExchangeAccountID: id, Venue: "BinanceUM", NativeID: "BTCUSDT:1",
			Payload: payload, FetchedAt: fetched,
		})
	})
	if err != nil {
		t.Fatal(err)
	}
	var made bool
	if err := db.QueryRow(ctx, `SELECT raw.ensure_month_partition('venue_trade', $1::date)`, fetched).Scan(&made); err != nil {
		t.Fatal(err)
	}
	if !made {
		t.Fatal("ensure made nothing")
	}
	var part string
	if err := db.QueryRow(ctx, `
		SELECT c.relname FROM pg_class c
		WHERE c.oid = (SELECT tableoid FROM raw.venue_trade LIMIT 1)`).Scan(&part); err != nil {
		t.Fatal(err)
	}
	if part != "venue_trade_202611" {
		t.Fatalf("row is in %s", part)
	}
	_, err = db.Exec(ctx, `UPDATE raw.venue_trade SET payload = '{}'`)
	var pgErr *pgconn.PgError
	if !errors.As(err, &pgErr) || pgErr.Message != "raw.venue_trade is immutable" {
		t.Fatalf("update err = %v", err)
	}
	_, err = db.Exec(ctx, `DELETE FROM raw.venue_trade`)
	if !errors.As(err, &pgErr) || pgErr.Message != "raw.venue_trade is immutable" {
		t.Fatalf("delete err = %v", err)
	}
}

type tradeText struct {
	native, order, price, qty, side string
	version                         int
}

func tradeTexts(t *testing.T, ctx context.Context, db *pgxpool.Pool, account int64) []tradeText {
	t.Helper()
	rows, err := db.Query(ctx, `
		SELECT native_id, order_native_id, price::text, qty::text, side, norm_version
		FROM ledger.trade WHERE exchange_account_id = $1 ORDER BY native_id`, account)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var out []tradeText
	for rows.Next() {
		var row tradeText
		if err := rows.Scan(&row.native, &row.order, &row.price, &row.qty, &row.side, &row.version); err != nil {
			t.Fatal(err)
		}
		out = append(out, row)
	}
	if err := rows.Err(); err != nil {
		t.Fatal(err)
	}
	return out
}

func sameTrades(a, b []tradeText) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
