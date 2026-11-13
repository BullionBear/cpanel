package credential

import (
	"context"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BullionBear/cpanel/internal/classify"
	"github.com/BullionBear/cpanel/internal/platform/sealbox"
	"github.com/BullionBear/cpanel/internal/platform/sealbox/open"
	"github.com/BullionBear/cpanel/internal/reconcile"
	"github.com/BullionBear/cpanel/internal/venue"
	"github.com/BullionBear/cpanel/internal/worker"
)

func TestConsecutiveAuthResetsOnSuccess(t *testing.T) {
	ctx, pool := testDB(t)
	key, err := open.GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	account := insertAccount(t, ctx, pool, "binance", "uid-1", "health")
	id := insertCredential(t, ctx, pool, key, account, "active", "key", "secret", "")
	authJob := func(dedup string) {
		t.Helper()
		if _, err := pool.Exec(ctx, `
			INSERT INTO ingest.job (kind, dedup_key, lease_key, params)
			VALUES ($1, $2, $3, jsonb_build_object('credential_id', $4::bigint))`,
			reconcile.CredentialProbeKind, dedup, reconcile.AccountLease(account), id); err != nil {
			t.Fatal(err)
		}
	}
	runAuth := func(dedup string) {
		t.Helper()
		authJob(dedup)
		stop := startWorker(t, &worker.Worker{
			DB: pool, Slots: 1, Poll: 20 * time.Millisecond, Grace: time.Second,
			Handlers: worker.Handlers{reconcile.CredentialProbeKind: func(context.Context, *worker.Job) (worker.Result, error) {
				return worker.Result{}, &classify.ClassifiedError{Class: classify.Auth, Status: http.StatusUnauthorized, Detail: "rejected by exchange"}
			}},
			CredentialHook: ApplyAuth,
		})
		waitCredential(t, ctx, pool, id, "active", 1)
		stop()
	}

	runAuth("auth-1")
	if rev(t, ctx, pool, account) != 1 {
		t.Fatalf("revision moved on the first failure: %d", rev(t, ctx, pool, account))
	}

	p := Probe{DB: pool, Key: key, Open: func(string, sealbox.Credentials) (Checker, error) {
		return &fakeChecker{check: venue.Check{UID: "uid-1", ReadOnly: true, Summary: "read"}}, nil
	}}
	if _, err := p.Handle(ctx, job(id)); err != nil {
		t.Fatal(err)
	}
	if status(t, ctx, pool, id) != "active" || failures(t, ctx, pool, id) != 0 || rev(t, ctx, pool, account) != 1 {
		t.Fatalf("after success status %s failures %d revision %d", status(t, ctx, pool, id), failures(t, ctx, pool, id), rev(t, ctx, pool, account))
	}

	runAuth("auth-2")
	authJob("auth-3")
	stop := startWorker(t, &worker.Worker{
		DB: pool, Slots: 1, Poll: 20 * time.Millisecond, Grace: time.Second,
		Handlers: worker.Handlers{reconcile.CredentialProbeKind: func(context.Context, *worker.Job) (worker.Result, error) {
			return worker.Result{}, &classify.ClassifiedError{Class: classify.Auth, Status: http.StatusUnauthorized, Detail: "rejected by exchange"}
		}},
		CredentialHook: ApplyAuth,
	})
	waitCredential(t, ctx, pool, id, "auth_failed", 2)
	stop()
	if rev(t, ctx, pool, account) != 2 || reason(t, ctx, pool, id) == "" {
		t.Fatalf("revision %d reason %q", rev(t, ctx, pool, account), reason(t, ctx, pool, id))
	}
	if auditHas(t, ctx, pool, "secret") {
		t.Fatal("audit contains the secret")
	}
}

func TestProbeRestoresActive(t *testing.T) {
	ctx, pool := testDB(t)
	key, err := open.GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	account := insertAccount(t, ctx, pool, "binance", "uid-1", "probe")
	enableKind(t, ctx, pool, account, "Binance")
	enableKind(t, ctx, pool, account, "BinanceUM")
	old := insertCredential(t, ctx, pool, key, account, "active", "key", "old", "")
	id := insertCredential(t, ctx, pool, key, account, "auth_failed", "key", "secret", "")
	if _, err := pool.Exec(ctx, `UPDATE pms.exchange_account_kind SET last_error = 'futures down' WHERE exchange_account_id = $1 AND kind = 'BinanceUM'`, account); err != nil {
		t.Fatal(err)
	}
	fake := &fakeChecker{check: venue.Check{UID: "uid-1", ReadOnly: true, Summary: "read"}}
	p := Probe{DB: pool, Key: key, Open: func(string, sealbox.Credentials) (Checker, error) { return fake, nil }}
	if _, err := p.Handle(ctx, &worker.Job{Kind: reconcile.CredentialProbeKind, Params: job(id).Params}); err != nil {
		t.Fatal(err)
	}
	if status(t, ctx, pool, id) != "active" || failures(t, ctx, pool, id) != 0 || status(t, ctx, pool, old) != "disabled" {
		t.Fatalf("restored %s old %s failures %d", status(t, ctx, pool, id), status(t, ctx, pool, old), failures(t, ctx, pool, id))
	}
	if rev(t, ctx, pool, account) != 2 {
		t.Fatalf("revision %d", rev(t, ctx, pool, account))
	}
	if fake.calls != 1 {
		t.Fatalf("account-layer calls %d", fake.calls)
	}
	if kindState(t, ctx, pool, account, "BinanceUM").err == "" {
		t.Fatal("probe cleared a kind error")
	}
}

func TestProbeAuthStaysFailed(t *testing.T) {
	ctx, pool := testDB(t)
	key, err := open.GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	account := insertAccount(t, ctx, pool, "binance", "uid-1", "stay")
	id := insertCredential(t, ctx, pool, key, account, "auth_failed", "key", "secret", "")
	p := Probe{DB: pool, Key: key, Open: func(string, sealbox.Credentials) (Checker, error) {
		return &fakeChecker{err: &classify.ClassifiedError{Class: classify.Auth, Status: http.StatusUnauthorized, Detail: "no"}}, nil
	}}
	_, err = p.Handle(ctx, job(id))
	if !classify.Is(err, classify.Auth) || status(t, ctx, pool, id) != "auth_failed" || rev(t, ctx, pool, account) != 1 {
		t.Fatalf("status %s revision %d err %v", status(t, ctx, pool, id), rev(t, ctx, pool, account), err)
	}
	var due time.Time
	if err := pool.QueryRow(ctx, `SELECT next_probe_at FROM pms.credential WHERE id = $1`, id).Scan(&due); err != nil {
		t.Fatal(err)
	}
	if due.Before(time.Now().Add(14 * time.Minute)) {
		t.Fatalf("next probe %s", due)
	}
}

func TestKindAndGlobalAuthDoNotTrip(t *testing.T) {
	ctx, pool := testDB(t)
	key, err := open.GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	account := insertAccount(t, ctx, pool, "binance", "uid-1", "kind")
	enableKind(t, ctx, pool, account, "Binance")
	enableKind(t, ctx, pool, account, "BinanceUM")
	id := insertCredential(t, ctx, pool, key, account, "active", "key", "secret", "")
	if _, err := pool.Exec(ctx, `
		INSERT INTO ingest.job (kind, dedup_key, lease_key, params)
		VALUES ('snapshot', 'snapshot:1', $1, jsonb_build_object('credential_id', $2::bigint)),
		       ('mftiko.sync', 'mftiko:1', 'global:mftiko', '{}')`,
		reconcile.AccountLease(account), id); err != nil {
		t.Fatal(err)
	}
	stop := startWorker(t, &worker.Worker{
		DB: pool, Slots: 2, Poll: 20 * time.Millisecond, Grace: time.Second,
		Handlers: worker.Handlers{
			"snapshot":    authFailure,
			"mftiko.sync": authFailure,
		},
		CredentialHook: ApplyAuth,
	})
	waitGone(t, ctx, pool)
	stop()
	if status(t, ctx, pool, id) != "active" || failures(t, ctx, pool, id) != 0 {
		t.Fatalf("status %s failures %d", status(t, ctx, pool, id), failures(t, ctx, pool, id))
	}
}

func TestIPBanDoesNotChangeCredential(t *testing.T) {
	ctx, pool := testDB(t)
	key, err := open.GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	account := insertAccount(t, ctx, pool, "binance", "uid-1", "ban")
	id := insertCredential(t, ctx, pool, key, account, "active", "key", "secret", "")
	if _, err := pool.Exec(ctx, `
		INSERT INTO ingest.job (kind, dedup_key, lease_key, params)
		VALUES ($1, 'ban:1', $2, jsonb_build_object('credential_id', $3::bigint))`,
		reconcile.CredentialProbeKind, reconcile.AccountLease(account), id); err != nil {
		t.Fatal(err)
	}
	hooked := make(chan struct{}, 1)
	stop := startWorker(t, &worker.Worker{
		DB: pool, Egress: "edge", Slots: 1, Poll: 20 * time.Millisecond, Grace: time.Second,
		Handlers: worker.Handlers{reconcile.CredentialProbeKind: func(context.Context, *worker.Job) (worker.Result, error) {
			return worker.Result{}, &classify.ClassifiedError{Class: classify.IPBanned, Status: http.StatusTeapot, Detail: "banned"}
		}},
		CredentialHook: func(context.Context, pgx.Tx, worker.AuthFailure) error {
			select {
			case hooked <- struct{}{}:
			default:
			}
			return nil
		},
	})
	waitGone(t, ctx, pool)
	stop()
	select {
	case <-hooked:
		t.Fatal("418 called the credential hook")
	default:
	}
	if status(t, ctx, pool, id) != "active" || failures(t, ctx, pool, id) != 0 {
		t.Fatalf("status %s failures %d", status(t, ctx, pool, id), failures(t, ctx, pool, id))
	}
	var n int
	if err := pool.QueryRow(ctx, `SELECT count(*) FROM ingest.pause WHERE scope = 'egress:edge'`).Scan(&n); err != nil || n != 1 {
		t.Fatalf("pauses %d err %v", n, err)
	}
}

func TestEmptyCiphertextDoesNotOpen(t *testing.T) {
	key, err := open.GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := open.OpenCredentials(key, nil, 1, 1); err == nil {
		t.Fatal("empty ciphertext opened")
	}
}

func TestEgressPauseSkipsProbe(t *testing.T) {
	ctx, pool := testDB(t)
	key, err := open.GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	account := insertAccount(t, ctx, pool, "binance", "uid-1", "paused")
	id := insertCredential(t, ctx, pool, key, account, "auth_failed", "key", "secret", "")
	if _, err := pool.Exec(ctx, `
		INSERT INTO ingest.job (kind, dedup_key, lease_key, params)
		VALUES ($1, $2, $3, jsonb_build_object('credential_id', $4::bigint))`,
		reconcile.CredentialProbeKind, reconcile.CredentialProbeDedup(id), reconcile.AccountLease(account), id); err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx, `INSERT INTO ingest.pause (scope, until, reason) VALUES ('egress:edge', now() + interval '1 hour', 'ip_banned')`); err != nil {
		t.Fatal(err)
	}
	hits := 0
	srv := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { hits++ }))
	t.Cleanup(srv.Close)
	stop := startWorker(t, &worker.Worker{
		DB: pool, Egress: "edge", Slots: 1, Poll: 20 * time.Millisecond,
		Handlers: worker.Handlers{reconcile.CredentialProbeKind: Probe{
			DB: pool, Key: key, Endpoints: Endpoints{BinanceSpot: srv.URL},
		}.Handle},
	})
	time.Sleep(250 * time.Millisecond)
	stop()
	if hits != 0 || status(t, ctx, pool, id) != "auth_failed" {
		t.Fatalf("hits %d status %s", hits, status(t, ctx, pool, id))
	}
}

func authFailure(context.Context, *worker.Job) (worker.Result, error) {
	return worker.Result{}, &classify.ClassifiedError{Class: classify.Auth, Status: http.StatusUnauthorized, Detail: "no"}
}

func startWorker(t *testing.T, w *worker.Worker) func() {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		w.Run(ctx)
	}()
	var once sync.Once
	stop := func() {
		once.Do(func() {
			cancel()
			select {
			case <-done:
			case <-time.After(5 * time.Second):
				t.Error("worker did not stop")
			}
		})
	}
	t.Cleanup(stop)
	return stop
}

func waitCredential(t *testing.T, ctx context.Context, pool *pgxpool.Pool, id int64, want string, wantFailures int) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if status(t, ctx, pool, id) == want && failures(t, ctx, pool, id) == wantFailures {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("credential %s failures %d, want %s %d", status(t, ctx, pool, id), failures(t, ctx, pool, id), want, wantFailures)
}

func waitGone(t *testing.T, ctx context.Context, pool *pgxpool.Pool) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		var n int
		if err := pool.QueryRow(ctx, `SELECT count(*) FROM ingest.job`).Scan(&n); err != nil {
			t.Fatal(err)
		}
		if n == 0 {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("jobs were not finished")
}

func failures(t *testing.T, ctx context.Context, pool *pgxpool.Pool, id int64) int {
	t.Helper()
	var n int
	if err := pool.QueryRow(ctx, `SELECT consecutive_auth_failures FROM pms.credential WHERE id = $1`, id).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}
