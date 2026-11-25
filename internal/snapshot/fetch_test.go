package snapshot

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BullionBear/cpanel/internal/classify"
	"github.com/BullionBear/cpanel/internal/platform/sealbox"
	"github.com/BullionBear/cpanel/internal/platform/sealbox/open"
	"github.com/BullionBear/cpanel/internal/reconcile"
	"github.com/BullionBear/cpanel/internal/snapshotkind"
	"github.com/BullionBear/cpanel/internal/venue/binance"
	"github.com/BullionBear/cpanel/internal/worker"
)

// balanceRow and positionRow follow the response examples of Futures Account Balance V3
// and Position Information V3 (docs/venues/binance-um.md §2). positionRow("ADAUSDT",
// "BOTH", "30") is the published example.
func balanceRow(asset, balance, available, cross, maxWithdraw string) string {
	return fmt.Sprintf(`{"accountAlias":"SgsR","asset":%q,"balance":%q,"crossWalletBalance":%q,"crossUnPnl":"0.00000000",`+
		`"availableBalance":%q,"maxWithdrawAmount":%q,"marginAvailable":true,"updateTime":1617939110373}`,
		asset, balance, cross, available, maxWithdraw)
}

func positionRow(symbol, side, amt string) string {
	return fmt.Sprintf(`{"symbol":%q,"positionSide":%q,"positionAmt":%q,"entryPrice":"0.385","breakEvenPrice":"0.385077",`+
		`"markPrice":"0.41047590","unRealizedProfit":"0.76427700","liquidationPrice":"0","isolatedMargin":"0",`+
		`"notional":"12.31427700","marginAsset":"USDT","isolatedWallet":"0","initialMargin":"0.61571385",`+
		`"maintMargin":"0.08004280","positionInitialMargin":"0.61571385","openOrderInitialMargin":"0","adl":2,`+
		`"bidNotional":"0","askNotional":"0","updateTime":1720736417660}`, symbol, side, amt)
}

func array(rows ...string) string { return "[" + strings.Join(rows, ",") + "]" }

type reply struct {
	status int
	body   string
}

// fapi is an httptest USD-M host. Replies are chosen by the api key header and the path;
// a key or path it was not given is a 404.
type fapi struct {
	t       *testing.T
	url     string
	mu      sync.Mutex
	secrets map[string]string           // api key -> secret, to check the signature
	replies map[string]map[string]reply // api key -> path -> reply
	paths   []string
}

func newFAPI(t *testing.T) *fapi {
	f := &fapi{t: t, secrets: map[string]string{}, replies: map[string]map[string]reply{}}
	srv := httptest.NewServer(http.HandlerFunc(f.serve))
	t.Cleanup(srv.Close)
	f.url = srv.URL
	return f
}

func (f *fapi) on(key, secret string, path string, status int, body string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.secrets[key] = secret
	if f.replies[key] == nil {
		f.replies[key] = map[string]reply{}
	}
	f.replies[key][path] = reply{status, body}
}

func (f *fapi) serve(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.paths = append(f.paths, r.URL.Path)
	if r.Method != http.MethodGet {
		f.t.Errorf("method %s", r.Method)
	}
	q := r.URL.Query()
	if q.Has("symbol") {
		f.t.Errorf("%s sent symbol", r.URL.Path)
	}
	key := r.Header.Get("X-MBX-APIKEY")
	signed := "recvWindow=" + q.Get("recvWindow") + "&timestamp=" + q.Get("timestamp")
	if secret, ok := f.secrets[key]; !ok || q.Get("signature") != binance.SignQuery(secret, signed) {
		f.t.Errorf("request with key %q is not signed by its secret", key)
	}
	rep, ok := f.replies[key][r.URL.Path]
	if !ok {
		w.WriteHeader(http.StatusNotFound)
		return
	}
	w.WriteHeader(rep.status)
	_, _ = w.Write([]byte(rep.body))
}

func (f *fapi) requests() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.paths)
}

// fakeCredential is an obviously fake api key and secret for one test account.
func fakeCredential(name string) sealbox.Credentials {
	return sealbox.Credentials{APIKey: "fake-key-" + name, APISecret: "fake-secret-" + name}
}

// addSealedAccount adds an account with an active read credential sealed to key.
func addSealedAccount(t *testing.T, ctx context.Context, db *pgxpool.Pool, key *open.PrivateKey, uid string, kinds []string, env string, cred sealbox.Credentials) int64 {
	t.Helper()
	var id int64
	if err := db.QueryRow(ctx, `
		INSERT INTO pms.exchange_account (exchange, exch_account_uid, label)
		VALUES ('binance', $1, $1) RETURNING id`, uid).Scan(&id); err != nil {
		t.Fatal(err)
	}
	for _, kind := range kinds {
		exec(t, ctx, db, `
			INSERT INTO pms.exchange_account_kind (exchange_account_id, kind) VALUES ($1, $2)`, id, kind)
	}
	var credID int64
	if err := db.QueryRow(ctx, `
		INSERT INTO pms.credential (
			exchange_account_id, purpose, status, ciphertext, api_key_last4, verified_at, exchange_env)
		VALUES ($1, 'read', 'active', '\x01', 'fake', $2, $3) RETURNING id`, id, hour.Add(-time.Hour), env).Scan(&credID); err != nil {
		t.Fatal(err)
	}
	sealed, err := sealbox.SealCredentials(key.Public(), id, credID, cred)
	if err != nil {
		t.Fatal(err)
	}
	exec(t, ctx, db, `UPDATE pms.credential SET ciphertext = $2 WHERE id = $1`, credID, sealed)
	return id
}

// outcome is how a snapshot job ended, as ingest.job_run recorded it.
type outcome struct {
	Status, Class, Error string
	Requests, Items      int
}

// runJobs queues a snapshot job for each account at bucket, runs one worker with f until
// they have all finished, and returns each account's latest run.
func runJobs(t *testing.T, ctx context.Context, db *pgxpool.Pool, f Fetch, bucket time.Time, ids ...int64) map[int64]outcome {
	t.Helper()
	finished := count(t, ctx, db, `SELECT count(*) FROM ingest.job_run WHERE kind = $1`, JobKind)
	for _, id := range ids {
		params, err := json.Marshal(map[string]any{"exchange_account_id": id, "bucket": bucket.UTC().Format(time.RFC3339)})
		if err != nil {
			t.Fatal(err)
		}
		exec(t, ctx, db, `
			INSERT INTO ingest.job (kind, dedup_key, lease_key, params) VALUES ($1, $2, $3, $4)`,
			JobKind, reconcile.SnapshotDedup(id, bucket), reconcile.AccountLease(id), params)
	}
	runWorker(t, ctx, db, f, finished+len(ids))
	out := map[int64]outcome{}
	for _, id := range ids {
		var o outcome
		if err := db.QueryRow(ctx, `
			SELECT status, COALESCE(error_class::text, ''), COALESCE(error, ''), requests, items
			FROM ingest.job_run WHERE dedup_key = $1
			ORDER BY finished_at DESC, id DESC LIMIT 1`, reconcile.SnapshotDedup(id, bucket)).
			Scan(&o.Status, &o.Class, &o.Error, &o.Requests, &o.Items); err != nil {
			t.Fatal(err)
		}
		out[id] = o
	}
	return out
}

func newKey(t *testing.T) *open.PrivateKey {
	t.Helper()
	key, err := open.GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	return key
}

func atTime(at time.Time) func() time.Time { return func() time.Time { return at } }

func count(t *testing.T, ctx context.Context, db *pgxpool.Pool, sql string, args ...any) int {
	t.Helper()
	var n int
	if err := db.QueryRow(ctx, sql, args...).Scan(&n); err != nil {
		t.Fatalf("%s: %v", sql, err)
	}
	return n
}

func batchesOf(t *testing.T, ctx context.Context, db *pgxpool.Pool, id int64) int {
	t.Helper()
	return count(t, ctx, db, `SELECT count(*) FROM ledger.snapshot_batch WHERE exchange_account_id = $1`, id)
}

func rawOf(t *testing.T, ctx context.Context, db *pgxpool.Pool, id int64) int {
	t.Helper()
	return count(t, ctx, db, `SELECT count(*) FROM raw.venue_snapshot WHERE exchange_account_id = $1`, id)
}

// ledgerRows reads the batch's detail rows in the order NormalizeUM keeps after sortRows.
func ledgerRows(t *testing.T, ctx context.Context, db *pgxpool.Pool, batch int64) Rows {
	t.Helper()
	rows, err := db.Query(ctx, `
		SELECT kind, wallet, asset, balance::text, available_balance::text, cross_wallet_balance::text, max_withdraw_amount::text
		FROM ledger.balance_snapshot WHERE batch_id = $1 AND norm_version = $2 ORDER BY asset`, batch, NormVersion)
	if err != nil {
		t.Fatal(err)
	}
	balances, err := pgx.CollectRows(rows, pgx.RowToStructByPos[Balance])
	if err != nil {
		t.Fatal(err)
	}
	rows, err = db.Query(ctx, `
		SELECT kind, venue, exch_ticker, position_side, position_amt::text, entry_price::text, mark_price::text,
		       unrealized_profit::text, isolated_margin::text, initial_margin::text, position_initial_margin::text,
		       isolated_wallet::text
		FROM ledger.position_snapshot WHERE batch_id = $1 AND norm_version = $2 ORDER BY exch_ticker, position_side`, batch, NormVersion)
	if err != nil {
		t.Fatal(err)
	}
	positions, err := pgx.CollectRows(rows, pgx.RowToStructByPos[Position])
	if err != nil {
		t.Fatal(err)
	}
	return Rows{Balances: balances, Positions: positions}
}

func sortRows(r Rows) Rows {
	slices.SortFunc(r.Balances, func(a, b Balance) int { return strings.Compare(a.Asset, b.Asset) })
	slices.SortFunc(r.Positions, func(a, b Position) int {
		return strings.Compare(a.ExchTicker+" "+a.PositionSide, b.ExchTicker+" "+b.PositionSide)
	})
	return r
}

func sameRows(a, b Rows) bool {
	return slices.Equal(a.Balances, b.Balances) && slices.Equal(a.Positions, b.Positions)
}

func TestFetchUM(t *testing.T) {
	ctx, db := testPool(t)
	key := newKey(t)
	srv := newFAPI(t)
	exec(t, ctx, db, `
		INSERT INTO mirror.symbol (
			uid, venue, category, exch_ticker, universal_ticker, base, quote, status, active, first_seen_at, last_seen_at)
		VALUES (42, 'BinanceUM', 'Perp', 'BTCUSDT', 'BinanceUM_Perp_BTCUSDT', 'BTC', 'USDT', 'TRADING', true, $1, $1),
		       (43, 'BinanceUM', 'Perp', 'ETHUSDT', 'BinanceUM_Perp_ETHUSDT', 'ETH', 'USDT', 'TRADING', true, $1, $1)`, hour)

	oneway := fakeCredential("oneway")
	hedge := fakeCredential("hedge")
	empty := fakeCredential("empty")
	balances := array(
		balanceRow("USDT", "122607.35137903", "23.72469206", "23.72469206", "23.72469206"),
		balanceRow("BNB", "0.00000000", "0.00000000", "0.00000000", "0.00000000"),
		balanceRow("BTC", "0", "0", "0", "0.00000001"))
	srv.on(oneway.APIKey, oneway.APISecret, binance.UMBalancePath, http.StatusOK, balances)
	srv.on(oneway.APIKey, oneway.APISecret, binance.UMPositionRiskPath, http.StatusOK, array(
		positionRow("ADAUSDT", "BOTH", "30"),
		positionRow("ETHUSDT", "BOTH", "-1.5"),
		positionRow("XRPUSDT", "BOTH", "0")))
	srv.on(hedge.APIKey, hedge.APISecret, binance.UMBalancePath, http.StatusOK, array(
		balanceRow("USDT", "10", "5", "10", "5")))
	srv.on(hedge.APIKey, hedge.APISecret, binance.UMPositionRiskPath, http.StatusOK, array(
		positionRow("BTCUSDT", "LONG", "0.500"),
		positionRow("BTCUSDT", "SHORT", "-0.200"),
		positionRow("ETHUSDT", "LONG", "0.000")))
	srv.on(empty.APIKey, empty.APISecret, binance.UMBalancePath, http.StatusOK, array(
		balanceRow("USDT", "0.00000000", "0.00000000", "0.00000000", "0.00000000")))
	srv.on(empty.APIKey, empty.APISecret, binance.UMPositionRiskPath, http.StatusOK, `[]`)

	onewayID := addSealedAccount(t, ctx, db, key, "oneway", []string{"Binance", "BinanceUM"}, "prod", oneway)
	hedgeID := addSealedAccount(t, ctx, db, key, "hedge", []string{"BinanceUM"}, "prod", hedge)
	emptyID := addSealedAccount(t, ctx, db, key, "empty", []string{"BinanceUM"}, "prod", empty)

	captured := hour.Add(time.Minute)
	f := Fetch{DB: db, Key: key, BinanceFutures: srv.url, Now: atTime(captured)}
	results := runJobs(t, ctx, db, f, hour, onewayID, hedgeID, emptyID)
	for id, o := range results {
		if o.Status != "succeeded" {
			t.Fatalf("account %d: %+v", id, o)
		}
	}

	type position struct {
		Ticker, Side, Amt string
		Category          *string
		UID               *int64
	}
	positionsOf := func(batch int64) []position {
		rows, err := db.Query(ctx, `
			SELECT exch_ticker, position_side, position_amt::text, category, uid
			FROM ledger.position_snapshot WHERE batch_id = $1 ORDER BY exch_ticker, position_side`, batch)
		if err != nil {
			t.Fatal(err)
		}
		out, err := pgx.CollectRows(rows, pgx.RowToStructByPos[position])
		if err != nil {
			t.Fatal(err)
		}
		return out
	}
	batchOf := func(id int64) int64 {
		t.Helper()
		var batch int64
		var at time.Time
		if err := db.QueryRow(ctx, `
			SELECT id, captured_at FROM ledger.snapshot_batch WHERE exchange_account_id = $1 AND bucket = $2`, id, hour).
			Scan(&batch, &at); err != nil {
			t.Fatal(err)
		}
		if !at.Equal(captured) {
			t.Fatalf("captured_at %s", at)
		}
		return batch
	}
	kindsOf := func(batch int64) string {
		rows, err := db.Query(ctx, `
			SELECT kind || '=' || status FROM ledger.snapshot_batch_kind WHERE batch_id = $1 ORDER BY kind`, batch)
		if err != nil {
			t.Fatal(err)
		}
		out, err := pgx.CollectRows(rows, pgx.RowTo[string])
		if err != nil {
			t.Fatal(err)
		}
		return strings.Join(out, ",")
	}
	str := func(s string) *string { return &s }
	i64 := func(n int64) *int64 { return &n }

	// One-way: BOTH keeps the sign; the zero row is an open order; ADAUSDT is not in the mirror.
	batch := batchOf(onewayID)
	if got := kindsOf(batch); got != "Binance=unimplemented,BinanceUM=succeeded" {
		t.Fatalf("one-way kinds %s", got)
	}
	got := positionsOf(batch)
	want := []position{
		{"ADAUSDT", "BOTH", "30", nil, nil},
		{"ETHUSDT", "BOTH", "-1.5", str("Perp"), i64(43)},
	}
	if len(got) != len(want) {
		t.Fatalf("one-way positions %+v", got)
	}
	for i := range want {
		g, w := got[i], want[i]
		if g.Ticker != w.Ticker || g.Side != w.Side || g.Amt != w.Amt ||
			(g.Category == nil) != (w.Category == nil) || (g.UID == nil) != (w.UID == nil) ||
			(g.Category != nil && *g.Category != *w.Category) || (g.UID != nil && *g.UID != *w.UID) {
			t.Fatalf("one-way position %d: %+v, want %+v", i, g, w)
		}
	}
	rows := ledgerRows(t, ctx, db, batch)
	if len(rows.Balances) != 2 || rows.Balances[0].Asset != "BTC" || rows.Balances[0].MaxWithdraw != "0.00000001" ||
		rows.Balances[1] != (Balance{Kind: "BinanceUM", Wallet: WalletUM, Asset: "USDT", Balance: "122607.35137903",
			AvailableBalance: "23.72469206", CrossWalletBalance: "23.72469206", MaxWithdraw: "23.72469206"}) {
		t.Fatalf("one-way balances %+v; an all-zero asset is left out", rows.Balances)
	}
	if rows.Positions[0] != (Position{Kind: "BinanceUM", Venue: "BinanceUM", ExchTicker: "ADAUSDT", PositionSide: "BOTH",
		PositionAmt: "30", EntryPrice: "0.385", MarkPrice: "0.41047590", UnrealizedProfit: "0.76427700",
		IsolatedMargin: "0", InitialMargin: "0.61571385", PositionInitialMargin: "0.61571385", IsolatedWallet: "0"}) {
		t.Fatalf("example position %+v", rows.Positions[0])
	}
	if res := results[onewayID]; res.Requests != 2 || res.Items != 1+2+2 {
		t.Fatalf("one-way result %+v", res)
	}

	// Raw: one row per call with the path only; normalizing it again gives the ledger rows.
	rawRows, err := db.Query(ctx, `
		SELECT venue, kind, bucket, params::text, payload::text FROM raw.venue_snapshot
		WHERE exchange_account_id = $1 ORDER BY params->>'path'`, onewayID)
	if err != nil {
		t.Fatal(err)
	}
	type rawRow struct {
		Venue, Kind     string
		Bucket          time.Time
		Params, Payload string
	}
	raw, err := pgx.CollectRows(rawRows, pgx.RowToStructByPos[rawRow])
	if err != nil {
		t.Fatal(err)
	}
	if len(raw) != 2 || raw[0].Params != `{"path": "/fapi/v3/balance"}` || raw[1].Params != `{"path": "/fapi/v3/positionRisk"}` {
		t.Fatalf("raw %+v", raw)
	}
	for _, r := range raw {
		if r.Venue != "BinanceUM" || r.Kind != "BinanceUM" || !r.Bucket.Equal(hour) {
			t.Fatalf("raw row %+v", r)
		}
		for _, secret := range []string{oneway.APIKey, oneway.APISecret, "signature", "timestamp"} {
			if strings.Contains(r.Params+r.Payload, secret) {
				t.Fatalf("raw row holds %q", secret)
			}
		}
	}
	if n := count(t, ctx, db, `SELECT count(*) FROM raw.venue_snapshot WHERE exchange_account_id = $1 AND payload = $2::jsonb`,
		onewayID, balances); n != 1 {
		t.Fatalf("raw balance payload is not the response body (%d)", n)
	}
	fromRaw, err := NormalizeUMRaw([]byte(raw[0].Payload), []byte(raw[1].Payload))
	if err != nil {
		t.Fatal(err)
	}
	again, err := NormalizeUMRaw([]byte(raw[0].Payload), []byte(raw[1].Payload))
	if err != nil {
		t.Fatal(err)
	}
	if !sameRows(sortRows(fromRaw), sortRows(again)) || !sameRows(sortRows(fromRaw), rows) {
		t.Fatalf("raw normalizes to %+v, ledger has %+v", fromRaw, rows)
	}

	// Hedge: LONG and SHORT kept as sent, mirror hit on both.
	batch = batchOf(hedgeID)
	got = positionsOf(batch)
	if len(got) != 2 || got[0].Side != "LONG" || got[0].Amt != "0.500" || got[1].Side != "SHORT" || got[1].Amt != "-0.200" ||
		got[0].UID == nil || *got[0].UID != 42 || got[1].Category == nil || *got[1].Category != "Perp" {
		t.Fatalf("hedge positions %+v", got)
	}
	if got := kindsOf(batch); got != "BinanceUM=succeeded" {
		t.Fatalf("hedge kinds %s", got)
	}

	// Empty: a batch and its kind result, no detail rows.
	batch = batchOf(emptyID)
	if rows := ledgerRows(t, ctx, db, batch); len(rows.Balances) != 0 || len(rows.Positions) != 0 {
		t.Fatalf("empty account rows %+v", rows)
	}
	if got := kindsOf(batch); got != "BinanceUM=succeeded" {
		t.Fatalf("empty kinds %s", got)
	}
	if n := rawOf(t, ctx, db, emptyID); n != 2 {
		t.Fatalf("empty account raw rows %d", n)
	}
	if res := results[emptyID]; res.Requests != 2 || res.Items != 1 {
		t.Fatalf("empty result %+v", res)
	}
	if n := srv.requests(); n != 6 {
		t.Fatalf("requests %d, want 2 per account", n)
	}

	// A batch already in place: the second run writes nothing more.
	second := runJobs(t, ctx, db, f, hour, emptyID)[emptyID]
	if second.Status != "succeeded" || second.Items != 0 || batchesOf(t, ctx, db, emptyID) != 1 || rawOf(t, ctx, db, emptyID) != 2 {
		t.Fatalf("second capture %+v", second)
	}
}

func TestFetchFailureWritesNothing(t *testing.T) {
	ctx, db := testPool(t)
	key := newKey(t)
	srv := newFAPI(t)
	ok := array(balanceRow("USDT", "1", "1", "1", "1"))
	// {cred} becomes the key and the secret: the stored error must not repeat them.
	cases := []struct {
		name              string
		balance, position reply
		class             classify.Class
		requests          int
	}{
		{"auth-401", reply{http.StatusUnauthorized, `{"code":-2015,"msg":"Invalid API-key, IP, or permissions for action. {cred}"}`}, reply{}, classify.Auth, 1},
		{"auth-2015", reply{http.StatusBadRequest, `{"code":-2015,"msg":"Invalid API-key, IP, or permissions for action. {cred}"}`}, reply{}, classify.Auth, 1},
		{"signature", reply{http.StatusBadRequest, `{"code":-1022,"msg":"Signature for this request is not valid. {cred}"}`}, reply{}, classify.Auth, 1},
		{"unavailable", reply{http.StatusOK, ok}, reply{http.StatusBadGateway, `bad gateway {cred}`}, classify.Unavailable, 2},
		{"waf", reply{http.StatusOK, ok}, reply{http.StatusForbidden, `{"code":-2015,"msg":"Invalid API-key {cred}"}`}, classify.RateLimited, 2},
		{"banned", reply{http.StatusTeapot, `{"code":-1003,"msg":"Way too many requests; IP banned {cred}"}`}, reply{}, classify.IPBanned, 1},
		{"unparsable", reply{http.StatusOK, `{"code":0}`}, reply{}, classify.BadRequest, 1},
		{"bad-side", reply{http.StatusOK, ok}, reply{http.StatusOK, array(positionRow("BTCUSDT", "BOTHWAYS", "1"))}, classify.BadRequest, 2},
	}
	ids := map[string]int64{}
	creds := map[string]sealbox.Credentials{}
	for _, c := range cases {
		cred := fakeCredential(c.name)
		body := func(r reply) string { return strings.ReplaceAll(r.body, "{cred}", cred.APIKey+" "+cred.APISecret) }
		srv.on(cred.APIKey, cred.APISecret, binance.UMBalancePath, c.balance.status, body(c.balance))
		if c.position.status != 0 {
			srv.on(cred.APIKey, cred.APISecret, binance.UMPositionRiskPath, c.position.status, body(c.position))
		}
		ids[c.name] = addSealedAccount(t, ctx, db, key, c.name, []string{"BinanceUM"}, "prod", cred)
		creds[c.name] = cred
	}
	var all []int64
	for _, id := range ids {
		all = append(all, id)
	}
	got := runJobs(t, ctx, db, Fetch{DB: db, Key: key, BinanceFutures: srv.url, Now: atTime(hour)}, hour, all...)
	for _, c := range cases {
		o, cred := got[ids[c.name]], creds[c.name]
		if o.Status != "failed" || o.Class != string(c.class) || o.Requests != c.requests || o.Items != 0 {
			t.Errorf("%s: %+v, want %s after %d requests", c.name, o, c.class, c.requests)
		}
		if strings.Contains(o.Error, cred.APIKey) || strings.Contains(o.Error, cred.APISecret) {
			t.Errorf("%s: job_run.error holds the credential: %q", c.name, o.Error)
		}
		if batchesOf(t, ctx, db, ids[c.name]) != 0 || rawOf(t, ctx, db, ids[c.name]) != 0 {
			t.Errorf("%s: a failed capture wrote a batch or raw", c.name)
		}
	}
}

func TestFetchCallsNothing(t *testing.T) {
	ctx, db := testPool(t)
	key := newKey(t)
	srv := newFAPI(t)

	late := addSealedAccount(t, ctx, db, key, "late", []string{"BinanceUM"}, "prod", fakeCredential("late"))
	if o := runJobs(t, ctx, db, Fetch{DB: db, Key: key, BinanceFutures: srv.url, Now: atTime(hour.Add(StaleAfter + time.Second))},
		hour, late)[late]; o != (outcome{Status: "succeeded"}) {
		t.Fatalf("late %+v", o)
	}

	deleted := addSealedAccount(t, ctx, db, key, "deleted", []string{"BinanceUM"}, "prod", fakeCredential("deleted"))
	exec(t, ctx, db, `UPDATE pms.exchange_account SET deleted_at = $2 WHERE id = $1`, deleted, hour)
	revoked := addSealedAccount(t, ctx, db, key, "revoked", []string{"BinanceUM"}, "prod", fakeCredential("revoked"))
	exec(t, ctx, db, `UPDATE pms.credential SET status = 'auth_failed' WHERE exchange_account_id = $1`, revoked)
	spot := addSealedAccount(t, ctx, db, key, "spot", []string{"Binance"}, "prod", fakeCredential("spot"))
	testnet := addSealedAccount(t, ctx, db, key, "testnet", []string{"BinanceUM"}, "testnet", fakeCredential("testnet"))
	other := addSealedAccount(t, ctx, db, newKey(t), "other-key", []string{"BinanceUM"}, "prod", fakeCredential("other-key"))
	got := runJobs(t, ctx, db, Fetch{DB: db, Key: key, BinanceFutures: srv.url, Now: atTime(hour.Add(time.Minute))},
		hour, deleted, revoked, spot, testnet, other)
	for name, id := range map[string]int64{"deleted": deleted, "revoked": revoked, "spot only": spot} {
		if o := got[id]; o != (outcome{Status: "succeeded"}) {
			t.Errorf("%s: %+v", name, o)
		}
	}
	if o := got[testnet]; o.Status != "failed" || o.Class != "internal" || o.Requests != 0 || !strings.Contains(o.Error, "testnet") {
		t.Errorf("environment mismatch %+v", o)
	}
	if o := got[other]; o.Status != "failed" || o.Class != "internal" || o.Requests != 0 {
		t.Errorf("unreadable credential %+v", o)
	}
	if n := srv.requests(); n != 0 {
		t.Fatalf("%d requests", n)
	}
	if n := count(t, ctx, db, `SELECT count(*) FROM ledger.snapshot_batch`); n != 0 {
		t.Fatalf("%d batches", n)
	}
}

func TestFailedHourThroughWorker(t *testing.T) {
	ctx, db := testPool(t)
	key := newKey(t)
	srv := newFAPI(t)
	cred := fakeCredential("worker")
	srv.on(cred.APIKey, cred.APISecret, binance.UMBalancePath, http.StatusUnauthorized,
		`{"code":-2015,"msg":"Invalid API-key, IP, or permissions for action."} `+cred.APIKey)
	id := addSealedAccount(t, ctx, db, key, "worker", []string{"BinanceUM"}, "prod", cred)
	exec(t, ctx, db, `
		INSERT INTO ledger.snapshot_batch (exchange_account_id, bucket, captured_at)
		VALUES ($1, $2, $3)`, id, hour.Add(-time.Hour), hour.Add(-time.Hour+time.Minute))
	if n := runRule(t, ctx, db, hour.Add(time.Minute)); n != 1 {
		t.Fatalf("rule inserted %d", n)
	}

	runWorker(t, ctx, db, Fetch{DB: db, Key: key, BinanceFutures: srv.url, Now: atTime(hour.Add(time.Minute))}, 1)
	var status, class, msg string
	var requests int
	if err := db.QueryRow(ctx, `
		SELECT status, COALESCE(error_class::text, ''), COALESCE(error, ''), requests
		FROM ingest.job_run WHERE dedup_key = $1`, reconcile.SnapshotDedup(id, hour)).
		Scan(&status, &class, &msg, &requests); err != nil {
		t.Fatal(err)
	}
	if status != "failed" || class != "auth" || requests != 1 ||
		strings.Contains(msg, cred.APIKey) || strings.Contains(msg, cred.APISecret) {
		t.Fatalf("job_run %s %s requests %d %q", status, class, requests, msg)
	}
	if n := count(t, ctx, db, `
		SELECT count(*) FROM ledger.snapshot_batch WHERE exchange_account_id = $1 AND bucket = $2`, id, hour); n != 0 {
		t.Fatal("a failed hour has a batch")
	}

	// Past StaleAfter the job calls nothing and the hour stays a gap.
	sent := srv.requests()
	if o := runJobs(t, ctx, db, Fetch{DB: db, Key: key, BinanceFutures: srv.url, Now: atTime(hour.Add(StaleAfter + time.Minute))},
		hour, id)[id]; o != (outcome{Status: "succeeded"}) || srv.requests() != sent {
		t.Fatalf("late retry %+v, %d requests", o, srv.requests()-sent)
	}
	if got := gaps(t, ctx, db, hour.Add(time.Hour+10*time.Minute)); len(got) != 1 || !got[0].Equal(hour) {
		t.Fatalf("gaps %v, want %s", got, hour)
	}
}

func TestMissedHourThroughWorker(t *testing.T) {
	ctx, db := testPool(t)
	key := newKey(t)
	srv := newFAPI(t)
	cred := fakeCredential("missed")
	srv.on(cred.APIKey, cred.APISecret, binance.UMBalancePath, http.StatusOK, `[]`)
	srv.on(cred.APIKey, cred.APISecret, binance.UMPositionRiskPath, http.StatusOK, `[]`)
	id := addSealedAccount(t, ctx, db, key, "missed", []string{"Binance", "BinanceUM"}, "prod", cred)
	if n := runRule(t, ctx, db, hour.Add(10*time.Minute)); n != 1 {
		t.Fatalf("first hour inserted %d", n)
	}
	returned := hour.Add(time.Hour + 10*time.Minute)
	if n := runRule(t, ctx, db, returned); n != 1 {
		t.Fatalf("second hour inserted %d; the missed hour must not be backfilled", n)
	}
	if got := gaps(t, ctx, db, returned); len(got) != 0 {
		t.Fatalf("gaps %v before the first batch", got)
	}

	runWorker(t, ctx, db, Fetch{DB: db, Key: key, BinanceFutures: srv.url, Now: atTime(returned)}, 2)
	var lateItems, onTimeItems int
	if err := db.QueryRow(ctx, `
		SELECT items FROM ingest.job_run WHERE dedup_key = $1 AND status = 'succeeded'`, reconcile.SnapshotDedup(id, hour)).Scan(&lateItems); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRow(ctx, `
		SELECT items FROM ingest.job_run WHERE dedup_key = $1 AND status = 'succeeded'`, reconcile.SnapshotDedup(id, hour.Add(time.Hour))).Scan(&onTimeItems); err != nil {
		t.Fatal(err)
	}
	if lateItems != 0 || onTimeItems != 1 || srv.requests() != 2 {
		t.Fatalf("items late=%d on time=%d, %d requests; the late job calls nothing", lateItems, onTimeItems, srv.requests())
	}
	if batchesOf(t, ctx, db, id) != 1 {
		t.Fatalf("batches %d", batchesOf(t, ctx, db, id))
	}
	if got := gaps(t, ctx, db, returned); len(got) != 0 {
		t.Fatalf("gaps %v: the hour before the first batch is not a gap", got)
	}
	if n := runRule(t, ctx, db, returned); n != 0 {
		t.Fatalf("rule inserted %d after the hour was written", n)
	}
	if got := gaps(t, ctx, db, hour.Add(3*time.Hour+10*time.Minute)); len(got) != 1 || !got[0].Equal(hour.Add(2*time.Hour)) {
		t.Fatalf("gaps %v, want the hour after the first batch that has none", got)
	}
}

func TestImplementedKindsHaveAFetch(t *testing.T) {
	srv := newFAPI(t)
	cred := fakeCredential("kinds")
	srv.on(cred.APIKey, cred.APISecret, binance.UMBalancePath, http.StatusOK, `[]`)
	srv.on(cred.APIKey, cred.APISecret, binance.UMPositionRiskPath, http.StatusOK, `[]`)
	f := Fetch{BinanceFutures: srv.url}
	for _, kind := range snapshotkind.Implemented() {
		if _, _, err := f.fetch(context.Background(), kind, cred); err != nil {
			t.Errorf("%s: %v", kind, err)
		}
	}
}

func TestNormalizeUMRejectsBadRows(t *testing.T) {
	good := positionRow("BTCUSDT", "BOTH", "1")
	cases := map[string][2]string{
		"missing amount":  {`[{"asset":"USDT","balance":"1","availableBalance":"1","crossWalletBalance":"1"}]`, `[]`},
		"empty asset":     {array(balanceRow("", "1", "1", "1", "1")), `[]`},
		"repeated asset":  {array(balanceRow("USDT", "1", "1", "1", "1"), balanceRow("USDT", "2", "2", "2", "2")), `[]`},
		"side":            {`[]`, array(positionRow("BTCUSDT", "", "1"))},
		"repeated side":   {`[]`, array(good, good)},
		"missing mark":    {`[]`, `[{"symbol":"BTCUSDT","positionSide":"BOTH","positionAmt":"1"}]`},
		"empty symbol":    {`[]`, array(positionRow("", "BOTH", "1"))},
		"not an array":    {`{}`, `[]`},
		"position object": {`[]`, `{"code":-1000}`},
	}
	for name, c := range cases {
		if _, err := NormalizeUMRaw([]byte(c[0]), []byte(c[1])); !classify.Is(err, classify.BadRequest) {
			t.Errorf("%s: %v", name, err)
		}
	}
	rows, err := NormalizeUMRaw([]byte(`[]`), []byte(`[]`))
	if err != nil || rows.Balances == nil || rows.Positions == nil || len(rows.Balances)+len(rows.Positions) != 0 {
		t.Fatalf("empty account %+v %v", rows, err)
	}
}

// runWorker runs one worker with f until n snapshot jobs have finished.
func runWorker(t *testing.T, ctx context.Context, db *pgxpool.Pool, f Fetch, n int) {
	t.Helper()
	w := &worker.Worker{
		DB: db, Name: "w1", Poll: 20 * time.Millisecond, Slots: 1,
		Handlers: worker.Handlers{JobKind: f.Handle},
	}
	ctxStop, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		w.Run(ctxStop)
	}()
	defer func() {
		cancel()
		<-done
	}()
	deadline := time.Now().Add(10 * time.Second)
	for count(t, ctx, db, `SELECT count(*) FROM ingest.job_run WHERE kind = $1`, JobKind) < n {
		if time.Now().After(deadline) {
			t.Fatal("snapshot jobs did not finish")
		}
		time.Sleep(20 * time.Millisecond)
	}
}
