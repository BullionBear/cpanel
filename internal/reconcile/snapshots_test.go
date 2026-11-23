package reconcile

import (
	"context"
	"sync"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

func addSnapshotAccount(t *testing.T, ctx context.Context, conn *pgx.Conn, exchange, uid string, kinds []string, status string, verified *time.Time) int64 {
	t.Helper()
	var id int64
	if err := conn.QueryRow(ctx, `
		INSERT INTO pms.exchange_account (exchange, exch_account_uid, label)
		VALUES ($1, $2, $2) RETURNING id`, exchange, uid).Scan(&id); err != nil {
		t.Fatal(err)
	}
	for _, kind := range kinds {
		exec(t, ctx, conn, `
			INSERT INTO pms.exchange_account_kind (exchange_account_id, kind) VALUES ($1, $2)`, id, kind)
	}
	if status != "" {
		exec(t, ctx, conn, `
			INSERT INTO pms.credential (
				exchange_account_id, purpose, status, ciphertext, api_key_last4, verified_at)
			VALUES ($1, 'read', $2, '\x01', 'abcd', $3)`, id, status, verified)
	}
	return id
}

func TestThreeLoopsOneSnapshotJob(t *testing.T) {
	ctx, dsn, conn := testDB(t)
	verified := t0
	id := addSnapshotAccount(t, ctx, conn, "binance", "uid-a", []string{"BinanceUM"}, "active", &verified)
	const loops = 3
	start := make(chan struct{})
	var wg sync.WaitGroup
	counts := make([]int, loops)
	errs := make([]error, loops)
	for i := range loops {
		pool, err := pgxpool.New(ctx, dsn)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(pool.Close)
		wg.Go(func() {
			<-start
			errs[i] = pgx.BeginFunc(ctx, pool, func(tx pgx.Tx) error {
				var err error
				counts[i], err = Snapshots([]string{"BinanceUM"}).Run(ctx, tx, t0.Add(10*time.Minute))
				return err
			})
		})
	}
	close(start)
	wg.Wait()
	sum := 0
	for i := range loops {
		if errs[i] != nil {
			t.Fatal(errs[i])
		}
		sum += counts[i]
	}
	if sum != 1 {
		t.Fatalf("inserted %d across 3 loops, want 1", sum)
	}
	got := jobs(t, ctx, conn)
	key := SnapshotDedup(id, t0)
	j, ok := got[key]
	if len(got) != 1 || !ok || j.Kind != SnapshotKind || j.LeaseKey != AccountLease(id) {
		t.Fatalf("jobs %+v, want one %s on %s", got, key, AccountLease(id))
	}
	if n := run(t, ctx, conn, Snapshots([]string{"BinanceUM"}), t0.Add(10*time.Minute)); n != 0 {
		t.Fatalf("second round inserted %d", n)
	}
}

func TestSnapshotDispatchFilters(t *testing.T) {
	ctx, _, conn := testDB(t)
	verified := t0
	ready := addSnapshotAccount(t, ctx, conn, "binance", "ready", []string{"Binance", "BinanceUM"}, "active", &verified)
	addSnapshotAccount(t, ctx, conn, "binance", "auth-failed", []string{"BinanceUM"}, "auth_failed", &verified)
	addSnapshotAccount(t, ctx, conn, "binance", "unverified", []string{"BinanceUM"}, "unverified", nil)
	addSnapshotAccount(t, ctx, conn, "binance", "spot-only", []string{"Binance"}, "active", &verified)
	disabled := addSnapshotAccount(t, ctx, conn, "binance", "disabled-kind", []string{"BinanceUM"}, "active", &verified)
	exec(t, ctx, conn, `
		UPDATE pms.exchange_account_kind SET disabled_at = $2
		WHERE exchange_account_id = $1`, disabled, t0)
	deleted := addSnapshotAccount(t, ctx, conn, "binance", "deleted", []string{"BinanceUM"}, "active", &verified)
	exec(t, ctx, conn, `UPDATE pms.exchange_account SET deleted_at = $2 WHERE id = $1`, deleted, t0)
	addSnapshotAccount(t, ctx, conn, "bybit", "bybit", []string{"Bybit"}, "active", &verified)

	if n := run(t, ctx, conn, Snapshots(nil), t0.Add(time.Minute)); n != 0 {
		t.Fatalf("empty kind list inserted %d", n)
	}
	if n := run(t, ctx, conn, Snapshots([]string{"BinanceUM"}), t0.Add(10*time.Minute)); n != 1 {
		t.Fatalf("inserted %d, want the one account with an active credential and BinanceUM", n)
	}
	got := jobs(t, ctx, conn)
	key := SnapshotDedup(ready, t0)
	if len(got) != 1 || got[key].Kind != SnapshotKind || got[key].LeaseKey != AccountLease(ready) {
		t.Fatalf("jobs %+v, want %s", got, key)
	}
	var bucket string
	if err := conn.QueryRow(ctx, `SELECT params->>'bucket' FROM ingest.job WHERE dedup_key = $1`, key).Scan(&bucket); err != nil {
		t.Fatal(err)
	}
	if bucket != t0.Format(time.RFC3339) {
		t.Fatalf("bucket param %s", bucket)
	}

	next := t0.Add(time.Hour)
	if n := run(t, ctx, conn, Snapshots([]string{"BinanceUM"}), next.Add(5*time.Minute)); n != 1 {
		t.Fatalf("next hour inserted %d, want 1; the past hour is not backfilled", n)
	}
	if _, ok := jobs(t, ctx, conn)[SnapshotDedup(ready, next)]; !ok {
		t.Fatalf("jobs %+v", jobs(t, ctx, conn))
	}
	if _, ok := jobs(t, ctx, conn)[key]; !ok {
		t.Fatal("the unfinished hour was removed")
	}
}

func TestSucceededSnapshotRunIsNotReinserted(t *testing.T) {
	ctx, _, conn := testDB(t)
	verified := t0
	late := addSnapshotAccount(t, ctx, conn, "binance", "late", []string{"BinanceUM"}, "active", &verified)
	failed := addSnapshotAccount(t, ctx, conn, "binance", "failed", []string{"BinanceUM"}, "active", &verified)
	exec(t, ctx, conn, `
		INSERT INTO ingest.job_run (
			id, kind, dedup_key, lease_key, priority, attempt, params, enqueued_at, finished_at, status)
		VALUES (1, 'snapshot', $1, $2, 100, 0, '{}', $3, $3, 'succeeded')`,
		SnapshotDedup(late, t0), AccountLease(late), t0.Add(20*time.Minute))
	exec(t, ctx, conn, `
		INSERT INTO ingest.job_run (
			id, kind, dedup_key, lease_key, priority, attempt, params, enqueued_at, finished_at, status, error_class)
		VALUES (2, 'snapshot', $1, $2, 100, 0, '{}', $3, $3, 'failed', 'unavailable')`,
		SnapshotDedup(failed, t0), AccountLease(failed), t0.Add(time.Minute))

	if n := run(t, ctx, conn, Snapshots([]string{"BinanceUM"}), t0.Add(30*time.Minute)); n != 1 {
		t.Fatalf("inserted %d, want only the account whose run failed", n)
	}
	got := jobs(t, ctx, conn)
	if _, ok := got[SnapshotDedup(late, t0)]; ok {
		t.Fatal("a succeeded run was dispatched again")
	}
	if _, ok := got[SnapshotDedup(failed, t0)]; !ok {
		t.Fatalf("jobs %+v", got)
	}
}

func TestSnapshotGaps(t *testing.T) {
	ctx, _, conn := testDB(t)
	verified := t0.Add(5 * time.Minute)
	covered := addSnapshotAccount(t, ctx, conn, "binance", "covered", []string{"BinanceUM"}, "active", &verified)
	addSnapshotAccount(t, ctx, conn, "binance", "spot", []string{"Binance"}, "active", &verified)
	gone := t0.Add(-time.Hour)
	addSnapshotAccount(t, ctx, conn, "binance", "no-key", []string{"BinanceUM"}, "auth_failed", &gone)
	asOf := t0.Add(time.Hour + 10*time.Minute)

	if got := gapBuckets(t, ctx, conn, []string{"BinanceUM"}, asOf); len(got) != 1 || !got[0].Equal(t0) {
		t.Fatalf("gaps %v, want only %s", got, t0)
	}
	if got := gapBuckets(t, ctx, conn, nil, asOf); len(got) != 0 {
		t.Fatalf("gaps with no implemented kind %v", got)
	}
	fresh := asOf
	onlyNow := addSnapshotAccount(t, ctx, conn, "binance", "just-verified", []string{"BinanceUM"}, "active", &fresh)
	if got := gapBuckets(t, ctx, conn, []string{"BinanceUM"}, asOf); len(got) != 1 || !got[0].Equal(t0) {
		t.Fatalf("gaps %v after an account verified in the current hour", got)
	}
	exec(t, ctx, conn, `
		INSERT INTO ledger.snapshot_batch (exchange_account_id, bucket, captured_at)
		VALUES ($1, $2, $3)`, covered, t0, t0.Add(10*time.Minute))
	if got := gapBuckets(t, ctx, conn, []string{"BinanceUM"}, asOf); len(got) != 0 {
		t.Fatalf("gaps %v after the batch, account %d should not add one", got, onlyNow)
	}
}

func gapBuckets(t *testing.T, ctx context.Context, conn *pgx.Conn, kinds []string, asOf time.Time) []time.Time {
	t.Helper()
	if kinds == nil {
		kinds = []string{}
	}
	rows, err := conn.Query(ctx, `
		SELECT bucket FROM ledger.snapshot_gaps($1::text[], $2) ORDER BY exchange_account_id, bucket`, kinds, asOf)
	if err != nil {
		t.Fatal(err)
	}
	out, err := pgx.CollectRows(rows, pgx.RowTo[time.Time])
	if err != nil {
		t.Fatal(err)
	}
	return out
}
