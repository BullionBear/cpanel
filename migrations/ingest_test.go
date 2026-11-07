package migrations

import (
	"context"
	"errors"
	"strconv"
	"testing"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"

	"github.com/BullionBear/cpanel/internal/platform/pgtest"
)

// The 0003 schema guarantees (C3-01, #64; C3-D001, C3-D002).

func ingestDB(t *testing.T) (context.Context, *pgx.Conn) {
	t.Helper()
	srv := pgtest.Start(t)
	ctx := testCtx(t)
	if _, err := Up(ctx, srv.DSN()); err != nil {
		t.Fatal(err)
	}
	return ctx, connect(t, srv.DSN())
}

func exec(t *testing.T, ctx context.Context, conn *pgx.Conn, sql string, args ...any) {
	t.Helper()
	if _, err := conn.Exec(ctx, sql, args...); err != nil {
		t.Fatalf("%s: %v", sql, err)
	}
}

// pgCode runs sql and returns the SQLSTATE it fails with, or "" when it succeeds.
func pgCode(t *testing.T, ctx context.Context, conn *pgx.Conn, sql string, args ...any) string {
	t.Helper()
	_, err := conn.Exec(ctx, sql, args...)
	if err == nil {
		return ""
	}
	var pgErr *pgconn.PgError
	if !errors.As(err, &pgErr) {
		t.Fatal(err)
	}
	return pgErr.Code
}

func count(t *testing.T, ctx context.Context, conn *pgx.Conn, sql string, args ...any) int {
	t.Helper()
	var n int
	if err := conn.QueryRow(ctx, sql, args...).Scan(&n); err != nil {
		t.Fatalf("%s: %v", sql, err)
	}
	return n
}

const (
	uniqueViolation = "23505"
	checkViolation  = "23514"
)

const insertStream = `
	INSERT INTO ingest.stream (kind, lease_key, key, source, period)
	VALUES ('test.counter', 'account:1', 'a', 'system', '1 minute') RETURNING id`

// finish moves the unfinished job with dedup_key into ingest.job_run, the way a worker completes one.
const finish = `
	WITH done AS (DELETE FROM ingest.job WHERE dedup_key = $1 RETURNING *)
	INSERT INTO ingest.job_run (id, kind, dedup_key, lease_key, stream_id, periodic_name, priority, attempt,
	                            params, enqueued_at, started_at, finished_at, worker, status, error_class, error)
	SELECT id, kind, dedup_key, lease_key, stream_id, periodic_name, priority, attempt,
	       params, enqueued_at, NULL, $2::timestamptz, 'w1', $3, $4, $5
	FROM done`

func TestOneUnfinishedJobPerDedupKeyAcrossDays(t *testing.T) {
	ctx, conn := ingestDB(t)
	var stream int64
	if err := conn.QueryRow(ctx, insertStream).Scan(&stream); err != nil {
		t.Fatal(err)
	}
	key := "stream:" + itoa(stream)
	enqueue := `INSERT INTO ingest.job (kind, dedup_key, lease_key, stream_id, enqueued_at)
	            VALUES ('test.counter', $1, 'account:1', $2, $3::timestamptz)`

	exec(t, ctx, conn, enqueue, key, stream, "2026-11-06T23:59:00Z")
	// A partitioned job table would only enforce this within one day (C3-D001).
	if code := pgCode(t, ctx, conn, enqueue, key, stream, "2026-11-07T00:01:00Z"); code != uniqueViolation {
		t.Fatalf("second unfinished job on the next day: SQLSTATE %q, want %s", code, uniqueViolation)
	}

	exec(t, ctx, conn, finish, key, "2026-11-07T00:00:30Z", "succeeded", nil, nil)
	exec(t, ctx, conn, enqueue, key, stream, "2026-11-07T00:01:00Z")
	if n := count(t, ctx, conn, `SELECT count(*) FROM ingest.job`); n != 1 {
		t.Fatalf("unfinished jobs = %d, want 1", n)
	}
	if n := count(t, ctx, conn, `SELECT count(*) FROM ingest.job_run`); n != 1 {
		t.Fatalf("finished jobs = %d, want 1", n)
	}
}

func TestJobConstraints(t *testing.T) {
	ctx, conn := ingestDB(t)
	exec(t, ctx, conn, `INSERT INTO ingest.periodic (name, kind, lease_key, period) VALUES ('p', 'maint.partitions', 'global:maint', '1 hour')`)
	var stream int64
	if err := conn.QueryRow(ctx, insertStream).Scan(&stream); err != nil {
		t.Fatal(err)
	}
	exec(t, ctx, conn, `INSERT INTO ingest.job (kind, dedup_key, lease_key) VALUES ('k', 'periodic:p', 'global:maint')`)

	for name, sql := range map[string]string{
		"running without a worker and lease token": `UPDATE ingest.job SET status = 'running', started_at = now() WHERE dedup_key = 'periodic:p'`,
		"stream and periodic at once": `INSERT INTO ingest.job (kind, dedup_key, lease_key, stream_id, periodic_name)
		                                VALUES ('k', 'x', 'l', ` + itoa(stream) + `, 'p')`,
		"failed run without an error class": `INSERT INTO ingest.job_run (id, kind, dedup_key, lease_key, priority, attempt, params, enqueued_at, finished_at, status)
		                                      VALUES (1, 'k', 'd', 'l', 1, 0, '{}', now(), now(), 'failed')`,
		"succeeded run with an error class": `INSERT INTO ingest.job_run (id, kind, dedup_key, lease_key, priority, attempt, params, enqueued_at, finished_at, status, error_class)
		                                      VALUES (2, 'k', 'd', 'l', 1, 0, '{}', now(), now(), 'succeeded', 'auth')`,
		"unknown error class":   `UPDATE ingest.stream SET last_error_class = 'oops'`,
		"paused without a time": `UPDATE ingest.stream SET paused_reason = 'bad_request'`,
	} {
		if code := pgCode(t, ctx, conn, sql); code != checkViolation {
			t.Errorf("%s: SQLSTATE %q, want %s", name, code, checkViolation)
		}
	}
	exec(t, ctx, conn, `UPDATE ingest.job SET status = 'running', started_at = now(), worker = 'w1', lease_token = 1 WHERE dedup_key = 'periodic:p'`)
}

func TestJobRunPartitions(t *testing.T) {
	ctx, conn := ingestDB(t)
	insertRun := `INSERT INTO ingest.job_run (id, kind, dedup_key, lease_key, priority, attempt, params, enqueued_at, finished_at, status, error_class)
	              VALUES ($1, 'k', 'd', 'l', 1, 0, '{}', now(), $2::timestamptz, $3, $4)`
	partitionOf := func(id int) string {
		t.Helper()
		var rel string
		if err := conn.QueryRow(ctx, `SELECT tableoid::regclass::text FROM ingest.job_run WHERE id = $1`, id).Scan(&rel); err != nil {
			t.Fatal(err)
		}
		return rel
	}

	// No daily partition yet: the row lands in the default partition instead of failing.
	exec(t, ctx, conn, insertRun, 1, "2026-11-07T00:00:30Z", "failed", "unavailable")
	exec(t, ctx, conn, insertRun, 2, "2026-11-07T12:00:00Z", "succeeded", nil)
	if p := partitionOf(1); p != "ingest.job_run_default" {
		t.Fatalf("row 1 in %s, want the default partition", p)
	}

	// Making the day's partition moves those rows out of the default one.
	var made bool
	if err := conn.QueryRow(ctx, `SELECT ingest.ensure_job_run_partition('2026-11-07')`).Scan(&made); err != nil || !made {
		t.Fatalf("ensure_job_run_partition = %v, %v; want true", made, err)
	}
	if err := conn.QueryRow(ctx, `SELECT ingest.ensure_job_run_partition('2026-11-07')`).Scan(&made); err != nil || made {
		t.Fatalf("second ensure_job_run_partition = %v, %v; want false", made, err)
	}
	for _, id := range []int{1, 2} {
		if p := partitionOf(id); p != "ingest.job_run_20261107" {
			t.Fatalf("row %d in %s, want ingest.job_run_20261107", id, p)
		}
	}
	// Days are UTC: 2026-11-08T00:30+08:00 is still 2026-11-07.
	exec(t, ctx, conn, insertRun, 3, "2026-11-08T00:30:00+08:00", "succeeded", nil)
	if p := partitionOf(3); p != "ingest.job_run_20261107" {
		t.Fatalf("row 3 in %s, want ingest.job_run_20261107", p)
	}
	if n := count(t, ctx, conn, `SELECT count(*) FROM pg_indexes WHERE schemaname = 'ingest' AND tablename = 'job_run_20261107'`); n != 4 {
		t.Fatalf("indexes on the new partition = %d, want 4 (primary key and three partitioned indexes)", n)
	}

	// An old failure still in the default partition, and a later day that stays.
	exec(t, ctx, conn, insertRun, 4, "2026-10-01T12:00:00Z", "failed", "auth")
	exec(t, ctx, conn, `SELECT ingest.ensure_job_run_partition('2026-11-08')`)
	exec(t, ctx, conn, insertRun, 5, "2026-11-08T12:00:00Z", "failed", "rate_limited")

	var dropped int
	if err := conn.QueryRow(ctx, `SELECT ingest.expire_job_runs('2026-11-08')`).Scan(&dropped); err != nil {
		t.Fatal(err)
	}
	if dropped != 1 {
		t.Fatalf("dropped %d partitions, want 1", dropped)
	}
	if n := count(t, ctx, conn, `SELECT count(*) FROM ingest.job_run`); n != 1 {
		t.Fatalf("job_run rows = %d, want 1 (the 2026-11-08 one)", n)
	}
	var failures []int
	rows, err := conn.Query(ctx, `SELECT id FROM ingest.job_failure ORDER BY id`)
	if err != nil {
		t.Fatal(err)
	}
	failures, err = pgx.CollectRows(rows, pgx.RowTo[int])
	if err != nil {
		t.Fatal(err)
	}
	if len(failures) != 2 || failures[0] != 1 || failures[1] != 4 {
		t.Fatalf("job_failure ids = %v, want [1 4]: failures before the cutoff, from a partition and from the default", failures)
	}
}

func itoa(n int64) string { return strconv.FormatInt(n, 10) }
