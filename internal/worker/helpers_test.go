package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"sync"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BullionBear/cpanel/internal/platform/pgtest"
	"github.com/BullionBear/cpanel/migrations"
)

// testDB starts a throwaway Postgres at the embedded schema version, with test.counter's tables.
func testDB(t *testing.T) (context.Context, string, *pgxpool.Pool) {
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
	// No unique key on purpose: a page written twice must show up as duplicate rows.
	exec(t, ctx, pool, `
		CREATE TABLE test_counter_item (stream_id BIGINT NOT NULL, seq INT NOT NULL);
		CREATE TABLE test_counter_page (id BIGSERIAL PRIMARY KEY, stream_id BIGINT NOT NULL, lease_key TEXT NOT NULL, worker TEXT NOT NULL,
		                                started_at TIMESTAMPTZ NOT NULL, ended_at TIMESTAMPTZ NOT NULL)`)
	return ctx, srv.DSN(), pool
}

func exec(t *testing.T, ctx context.Context, db *pgxpool.Pool, sql string, args ...any) {
	t.Helper()
	if _, err := db.Exec(ctx, sql, args...); err != nil {
		t.Fatalf("%s: %v", sql, err)
	}
}

func queryInt(t *testing.T, ctx context.Context, db *pgxpool.Pool, sql string, args ...any) int {
	t.Helper()
	var n int
	if err := db.QueryRow(ctx, sql, args...).Scan(&n); err != nil {
		t.Fatalf("%s: %v", sql, err)
	}
	return n
}

// addStream inserts a stream due now, and its job when withJob is set, as the ReconcileLoop would.
func addStream(t *testing.T, ctx context.Context, db *pgxpool.Pool, kind, leaseKey, key string, withJob bool) int64 {
	t.Helper()
	var id int64
	err := db.QueryRow(ctx, `
		INSERT INTO ingest.stream (kind, lease_key, key, source, period, next_due_at)
		VALUES ($1, $2, $3, 'system', '1 hour', now()) RETURNING id`, kind, leaseKey, key).Scan(&id)
	if err != nil {
		t.Fatal(err)
	}
	if withJob {
		exec(t, ctx, db, `INSERT INTO ingest.job (kind, dedup_key, lease_key, stream_id) VALUES ($1, $2, $3, $4)`,
			kind, fmt.Sprintf("stream:%d", id), leaseKey, id)
	}
	return id
}

// waitFor polls cond until it holds, failing the test after timeout.
func waitFor(t *testing.T, timeout time.Duration, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for !cond() {
		if time.Now().After(deadline) {
			t.Fatalf("no %s within %s", what, timeout)
		}
		time.Sleep(20 * time.Millisecond)
	}
}

// start runs w until the test ends or the returned stop is called; stop waits for Run to return.
func start(t *testing.T, w *Worker) (stop func()) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		w.Run(ctx)
	}()
	var once sync.Once
	stop = func() {
		once.Do(func() {
			cancel()
			select {
			case <-done:
			case <-time.After(30 * time.Second):
				t.Error("Run did not return within 30s of its context ending")
			}
		})
	}
	t.Cleanup(stop)
	return stop
}

const counterKind = "test.counter"

// counter is test.counter: a fake stream whose cursor is {"n": rows written}. Each page is one request that takes
// delay and writes rows n+1 … n+pageSize, until target.
type counter struct {
	worker           string
	pageSize, target int
	delay            time.Duration
}

func (c counter) handle(ctx context.Context, job *Job) (Result, error) {
	var cur struct {
		N int `json:"n"`
	}
	if job.Cursor != nil {
		if err := json.Unmarshal(job.Cursor, &cur); err != nil {
			return Result{}, err
		}
	}
	var res Result
	for cur.N < c.target {
		if !job.Continue(res.Requests) {
			res.More = true
			return res, nil
		}
		started := time.Now()
		res.Requests++
		select {
		case <-time.After(c.delay):
		case <-ctx.Done():
			return res, ctx.Err()
		}
		n := min(c.pageSize, c.target-cur.N)
		err := job.CommitPage(ctx, func(tx pgx.Tx) error {
			if _, err := tx.Exec(ctx, `INSERT INTO test_counter_item (stream_id, seq) SELECT $1, g FROM generate_series($2::int, $3::int) g`,
				job.StreamID, cur.N+1, cur.N+n); err != nil {
				return err
			}
			_, err := tx.Exec(ctx, `INSERT INTO test_counter_page (stream_id, lease_key, worker, started_at, ended_at) VALUES ($1, $2, $3, $4, $5)`,
				job.StreamID, job.LeaseKey, c.worker, started, time.Now())
			return err
		}, map[string]int{"n": cur.N + n})
		if err != nil {
			return res, err
		}
		cur.N += n
		res.Items += n
	}
	return res, nil
}

// checkStream fails the test unless the stream has rows 1 … want exactly once each and its cursor is {"n": want}.
func checkStream(t *testing.T, ctx context.Context, db *pgxpool.Pool, id int64, want int) {
	t.Helper()
	var rows, distinct, lo, hi, n int
	err := db.QueryRow(ctx, `
		SELECT count(*), count(DISTINCT seq), coalesce(min(seq), 0), coalesce(max(seq), 0),
		       coalesce((SELECT (cursor->>'n')::int FROM ingest.stream WHERE id = $1), 0)
		FROM test_counter_item WHERE stream_id = $1`, id).Scan(&rows, &distinct, &lo, &hi, &n)
	if err != nil {
		t.Fatal(err)
	}
	if rows != want || distinct != want || (want > 0 && (lo != 1 || hi != want)) || n != want {
		t.Errorf("stream %d: %d rows, %d distinct, seq %d…%d, cursor %d; want %d of each, 1…%d", id, rows, distinct, lo, hi, n, want, want)
	}
}
