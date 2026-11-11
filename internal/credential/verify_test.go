package credential

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BullionBear/cpanel/internal/classify"
	"github.com/BullionBear/cpanel/internal/platform/pgtest"
	"github.com/BullionBear/cpanel/internal/platform/sealbox"
	"github.com/BullionBear/cpanel/internal/platform/sealbox/open"
	"github.com/BullionBear/cpanel/internal/reconcile"
	"github.com/BullionBear/cpanel/internal/venue"
	"github.com/BullionBear/cpanel/internal/worker"
	"github.com/BullionBear/cpanel/migrations"
)

func TestVerifyState(t *testing.T) {
	ctx, pool := testDB(t)
	key, err := open.GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	const apiKey = "LEAKEDKEY1234"
	account := insertAccount(t, ctx, pool, "binance", "uid-1", "desk")
	enableKind(t, ctx, pool, account, "Binance")
	enableKind(t, ctx, pool, account, "BinanceUM")
	old := insertCredential(t, ctx, pool, key, account, "active", apiKey, "old-secret", "")
	fresh := insertCredential(t, ctx, pool, key, account, "unverified", apiKey, "new-secret", "")

	fake := &fakeChecker{check: venue.Check{UID: "uid-1", ReadOnly: true, Summary: "enableReading=true"},
		families: map[string]error{"fapi": &classify.ClassifiedError{Class: classify.Unavailable, Status: 500, Detail: "futures down " + apiKey}}}
	v := Verify{DB: pool, Key: key, Open: func(exchange string, cred sealbox.Credentials) (Checker, error) {
		if exchange != "binance" || cred.APIKey != apiKey || cred.APISecret != "new-secret" {
			t.Fatalf("opened %s %q", exchange, cred.APIKey)
		}
		return fake, nil
	}}
	if _, err := v.Handle(ctx, job(fresh)); err != nil {
		t.Fatal(err)
	}
	if status(t, ctx, pool, fresh) != "active" || status(t, ctx, pool, old) != "disabled" {
		t.Fatalf("fresh %s old %s", status(t, ctx, pool, fresh), status(t, ctx, pool, old))
	}
	if got := kindState(t, ctx, pool, account, "Binance"); got.err != "" || got.verified == nil {
		t.Fatalf("spot kind %+v", got)
	}
	um := kindState(t, ctx, pool, account, "BinanceUM")
	if um.verified != nil || um.err == "" || contains(um.err, apiKey) {
		t.Fatalf("futures kind %+v", um)
	}
	if rev(t, ctx, pool, account) != 2 {
		t.Fatalf("revision %d, want 2", rev(t, ctx, pool, account))
	}
	if auditHas(t, ctx, pool, apiKey) {
		t.Fatal("audit contains the api key")
	}

	second := insertCredential(t, ctx, pool, key, account, "unverified", apiKey, "rejected-secret", "")
	v.Open = func(string, sealbox.Credentials) (Checker, error) {
		return &fakeChecker{check: venue.Check{UID: "other", ReadOnly: true, Summary: "read"}}, nil
	}
	if _, err := v.Handle(ctx, job(second)); err != nil {
		t.Fatal(err)
	}
	if status(t, ctx, pool, second) != "rejected" || status(t, ctx, pool, fresh) != "active" {
		t.Fatalf("rejected %s active %s", status(t, ctx, pool, second), status(t, ctx, pool, fresh))
	}
	if reason(t, ctx, pool, second) == "" || contains(reason(t, ctx, pool, second), apiKey) {
		t.Fatalf("reason %q", reason(t, ctx, pool, second))
	}
	if rev(t, ctx, pool, account) != 2 {
		t.Fatalf("revision changed on reject: %d", rev(t, ctx, pool, account))
	}

	third := insertCredential(t, ctx, pool, key, account, "unverified", apiKey, "auth-secret", "")
	v.Open = func(string, sealbox.Credentials) (Checker, error) {
		return &fakeChecker{err: &classify.ClassifiedError{Class: classify.Auth, Status: 401, Detail: "no"}}, nil
	}
	if _, err := v.Handle(ctx, job(third)); !classify.Is(err, classify.Auth) || status(t, ctx, pool, third) != "unverified" {
		t.Fatalf("status %s err %v", status(t, ctx, pool, third), err)
	}
}

func TestVerifyBinanceHTTP(t *testing.T) {
	ctx, pool := testDB(t)
	key, err := open.GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	account := insertAccount(t, ctx, pool, "binance", "42", "http")
	enableKind(t, ctx, pool, account, "Binance")
	enableKind(t, ctx, pool, account, "BinanceUM")
	id := insertCredential(t, ctx, pool, key, account, "unverified", "key", "secret", "")
	var paths []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			t.Errorf("method %s", r.Method)
		}
		paths = append(paths, r.URL.Path)
		switch r.URL.Path {
		case "/api/v3/account":
			_, _ = w.Write([]byte(`{"uid":42,"canTrade":true}`))
		case "/sapi/v1/account/apiRestrictions":
			_, _ = w.Write([]byte(`{"enableReading":true,"enableFutures":false,"enableWithdrawals":false,"enableSpotAndMarginTrading":false}`))
		case "/fapi/v2/account":
			http.Error(w, "futures down", http.StatusInternalServerError)
		default:
			t.Errorf("path %s", r.URL.Path)
		}
	}))
	t.Cleanup(srv.Close)
	v := Verify{DB: pool, Key: key, Endpoints: Endpoints{BinanceSpot: srv.URL, BinanceFutures: srv.URL}}
	if _, err := v.Handle(ctx, job(id)); err != nil {
		t.Fatal(err)
	}
	if status(t, ctx, pool, id) != "active" {
		t.Fatalf("status %s", status(t, ctx, pool, id))
	}
	if kindState(t, ctx, pool, account, "Binance").verified == nil {
		t.Fatal("spot kind was not verified")
	}
	if kindState(t, ctx, pool, account, "BinanceUM").err == "" {
		t.Fatal("futures kind has no error")
	}
	if len(paths) != 3 || paths[2] != "/fapi/v2/account" {
		t.Fatalf("paths %v", paths)
	}
}

func TestNotReadOnlyIsRejected(t *testing.T) {
	ctx, pool := testDB(t)
	key, err := open.GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	account := insertAccount(t, ctx, pool, "binance", "42", "flags")
	id := insertCredential(t, ctx, pool, key, account, "unverified", "key", "secret", "")
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v3/account" {
			_, _ = w.Write([]byte(`{"uid":42}`))
			return
		}
		_, _ = w.Write([]byte(`{"enableReading":true,"enableFutures":true}`))
	}))
	t.Cleanup(srv.Close)
	v := Verify{DB: pool, Key: key, Endpoints: Endpoints{BinanceSpot: srv.URL}}
	if _, err := v.Handle(ctx, job(id)); err != nil {
		t.Fatal(err)
	}
	if status(t, ctx, pool, id) != "rejected" || reason(t, ctx, pool, id) == "" {
		t.Fatalf("status %s reason %q", status(t, ctx, pool, id), reason(t, ctx, pool, id))
	}
}

func TestEgressPauseDoesNotCall(t *testing.T) {
	ctx, pool := testDB(t)
	key, err := open.GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	account := insertAccount(t, ctx, pool, "binance", "42", "paused")
	id := insertCredential(t, ctx, pool, key, account, "unverified", "key", "secret", "")
	if _, err := pool.Exec(ctx, `
		INSERT INTO ingest.job (kind, dedup_key, lease_key, params)
		VALUES ($1, $2, $3, jsonb_build_object('credential_id', $4::bigint))`,
		reconcile.CredentialVerifyKind, reconcile.CredentialDedup(id), reconcile.AccountLease(account), id); err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx, `INSERT INTO ingest.pause (scope, until, reason) VALUES ('egress:edge', now() + interval '1 hour', 'ip_banned')`); err != nil {
		t.Fatal(err)
	}
	hits := 0
	srv := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { hits++ }))
	t.Cleanup(srv.Close)
	w := &worker.Worker{
		DB: pool, Egress: "edge", Slots: 1, Poll: 20 * time.Millisecond,
		Handlers: worker.Handlers{reconcile.CredentialVerifyKind: Verify{
			DB: pool, Key: key, Endpoints: Endpoints{BinanceSpot: srv.URL, BinanceFutures: srv.URL},
		}.Handle},
	}
	runCtx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		w.Run(runCtx)
	}()
	time.Sleep(250 * time.Millisecond)
	cancel()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("worker did not stop")
	}
	if hits != 0 || status(t, ctx, pool, id) != "unverified" {
		t.Fatalf("hits %d status %s", hits, status(t, ctx, pool, id))
	}
}

type fakeChecker struct {
	check    venue.Check
	err      error
	families map[string]error
	calls    int
}

func (f *fakeChecker) CheckAccount(context.Context) (venue.Check, error) {
	f.calls++
	return f.check, f.err
}

func (f *fakeChecker) ReadFamily(_ context.Context, family string) error {
	f.calls++
	if err, ok := f.families[family]; ok {
		return err
	}
	return nil
}

func (f *fakeChecker) Calls() int { return f.calls }

func testDB(t *testing.T) (context.Context, *pgxpool.Pool) {
	t.Helper()
	srv := pgtest.Start(t)
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
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

func insertAccount(t *testing.T, ctx context.Context, pool *pgxpool.Pool, exchange, uid, label string) int64 {
	t.Helper()
	var id int64
	err := pool.QueryRow(ctx, `
		INSERT INTO pms.exchange_account (exchange, exch_account_uid, label)
		VALUES ($1, $2, $3) RETURNING id`, exchange, uid, label).Scan(&id)
	if err != nil {
		t.Fatal(err)
	}
	return id
}

func enableKind(t *testing.T, ctx context.Context, pool *pgxpool.Pool, account int64, kind string) {
	t.Helper()
	if _, err := pool.Exec(ctx, `INSERT INTO pms.exchange_account_kind (exchange_account_id, kind) VALUES ($1, $2)`, account, kind); err != nil {
		t.Fatal(err)
	}
}

func insertCredential(t *testing.T, ctx context.Context, pool *pgxpool.Pool, key *open.PrivateKey, account int64, state, apiKey, secret, passphrase string) int64 {
	t.Helper()
	var id int64
	err := pool.QueryRow(ctx, `
		INSERT INTO pms.credential (exchange_account_id, purpose, status, ciphertext, api_key_last4)
		VALUES ($1, 'read', $2, '\x01', '1234') RETURNING id`, account, state).Scan(&id)
	if err != nil {
		t.Fatal(err)
	}
	sealed, err := sealbox.SealCredentials(key.Public(), account, id, sealbox.Credentials{APIKey: apiKey, APISecret: secret, Passphrase: passphrase})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx, `UPDATE pms.credential SET ciphertext = $2 WHERE id = $1`, id, sealed); err != nil {
		t.Fatal(err)
	}
	return id
}

func job(id int64) *worker.Job {
	return &worker.Job{ID: id, Kind: reconcile.CredentialVerifyKind, Params: []byte(`{"credential_id":` + strconv.FormatInt(id, 10) + `}`)}
}

func status(t *testing.T, ctx context.Context, pool *pgxpool.Pool, id int64) string {
	t.Helper()
	var s string
	if err := pool.QueryRow(ctx, `SELECT status FROM pms.credential WHERE id = $1`, id).Scan(&s); err != nil {
		t.Fatal(err)
	}
	return s
}

func reason(t *testing.T, ctx context.Context, pool *pgxpool.Pool, id int64) string {
	t.Helper()
	var s *string
	if err := pool.QueryRow(ctx, `SELECT failure_reason FROM pms.credential WHERE id = $1`, id).Scan(&s); err != nil {
		t.Fatal(err)
	}
	if s == nil {
		return ""
	}
	return *s
}

func rev(t *testing.T, ctx context.Context, pool *pgxpool.Pool, id int64) int64 {
	t.Helper()
	var n int64
	if err := pool.QueryRow(ctx, `SELECT revision FROM pms.exchange_account WHERE id = $1`, id).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

type kindView struct {
	verified *time.Time
	err      string
}

func kindState(t *testing.T, ctx context.Context, pool *pgxpool.Pool, account int64, kind string) kindView {
	t.Helper()
	var v kindView
	var errText *string
	if err := pool.QueryRow(ctx, `
		SELECT verified_at, last_error FROM pms.exchange_account_kind
		WHERE exchange_account_id = $1 AND kind = $2`, account, kind).Scan(&v.verified, &errText); err != nil {
		t.Fatal(err)
	}
	if errText != nil {
		v.err = *errText
	}
	return v
}

func auditHas(t *testing.T, ctx context.Context, pool *pgxpool.Pool, secret string) bool {
	t.Helper()
	var n int
	err := pool.QueryRow(ctx, `SELECT count(*) FROM meta.audit WHERE params::text LIKE '%' || $1 || '%' OR coalesce(result::text, '') LIKE '%' || $1 || '%'`, secret).Scan(&n)
	if err != nil {
		t.Fatal(err)
	}
	return n > 0
}

func contains(s, sub string) bool { return strings.Contains(s, sub) }
