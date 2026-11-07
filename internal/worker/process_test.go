package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"math/rand/v2"
	"os"
	"os/signal"
	"syscall"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BullionBear/cpanel/internal/cmdtest"
	"github.com/BullionBear/cpanel/internal/platform/logger"
	"github.com/BullionBear/cpanel/internal/reconcile"
)

// childEnv names the variable that makes the test binary run as a worker process instead of running the tests:
// the acceptance tests below start, kill and stop real processes.
const childEnv = "CPANEL_TEST_WORKER"

// childConfig is a worker process's configuration, as JSON in childEnv.
type childConfig struct {
	DSN, Name                string
	Slots, BudgetRequests    int
	TTL, Renew, Poll, Budget time.Duration
	PageSize, Target         int
	Delay                    time.Duration
}

func TestMain(m *testing.M) {
	if cfg := os.Getenv(childEnv); cfg != "" {
		os.Exit(runChild(cfg))
	}
	os.Exit(m.Run())
}

// runChild runs a worker with test.counter until SIGTERM or SIGINT.
func runChild(cfg string) int {
	var c childConfig
	if err := json.Unmarshal([]byte(cfg), &c); err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 2
	}
	log := logger.New(os.Stderr, "worker-test", logger.DefaultLevel)
	pool, err := pgxpool.New(context.Background(), c.DSN)
	if err != nil {
		log.Error().Err(err).Msg("database")
		return 1
	}
	defer pool.Close()
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()
	w := &Worker{
		DB: pool, Name: c.Name, Slots: c.Slots, LeaseTTL: c.TTL, RenewEvery: c.Renew, Poll: c.Poll,
		BudgetRequests: c.BudgetRequests, BudgetTime: c.Budget, Log: log,
		Handlers: Handlers{counterKind: counter{worker: c.Name, pageSize: c.PageSize, target: c.Target, delay: c.Delay}.handle},
	}
	w.Run(ctx)
	return 0
}

func startChild(t *testing.T, c childConfig) *cmdtest.Process {
	t.Helper()
	b, err := json.Marshal(c)
	if err != nil {
		t.Fatal(err)
	}
	p := cmdtest.Start(t, os.Args[0], []string{childEnv + "=" + string(b)})
	p.Log(t, "worker started")
	return p
}

// dispatch runs the ReconcileLoop's stream rule until the test ends, so a stream left with more to do gets its
// next job, as in production.
func dispatch(t *testing.T, db *pgxpool.Pool) {
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		(&reconcile.Loop{DB: db, Rules: []reconcile.Rule{reconcile.Streams}, Interval: 50 * time.Millisecond}).Run(ctx)
	}()
	t.Cleanup(func() {
		cancel()
		<-done
	})
}

// TestKilledWorker: two worker processes run six streams on three lease keys; one is killed with SIGKILL while it
// runs a job. The other takes its leases over once they expire and every stream ends with each row written
// exactly once, its cursor equal to its row count, and no two pages of one lease key running at the same time.
func TestKilledWorker(t *testing.T) {
	ctx, dsn, db := testDB(t)
	const target, pageSize = 45, 3
	var streams []int64
	for i := range 6 {
		streams = append(streams, addStream(t, ctx, db, counterKind, fmt.Sprintf("account:%d", i%3+1), fmt.Sprint(i), false))
	}
	dispatch(t, db)
	cfg := childConfig{
		DSN: dsn, Slots: 2, TTL: 2 * time.Second, Renew: 500 * time.Millisecond, Poll: 50 * time.Millisecond,
		BudgetRequests: 4, Budget: 10 * time.Second, PageSize: pageSize, Target: target, Delay: 30 * time.Millisecond,
	}
	cfg.Name = "a"
	a := startChild(t, cfg)
	cfg.Name = "b"
	b := startChild(t, cfg)
	children := map[string]*cmdtest.Process{"a": a, "b": b}

	seed := uint64(time.Now().UnixNano())
	t.Logf("seed %d", seed)
	rnd := rand.New(rand.NewPCG(seed, seed))
	after := 10 + rnd.IntN(40)
	waitFor(t, 30*time.Second, fmt.Sprintf("%d pages", after), func() bool {
		return queryInt(t, ctx, db, `SELECT count(*) FROM test_counter_page`) >= after
	})
	// Freeze a random worker; kill it once it is caught holding a running job, so the kill always lands mid-job.
	var victim string
	waitFor(t, 30*time.Second, "a worker frozen mid-job", func() bool {
		name := []string{"a", "b"}[rnd.IntN(2)]
		p := children[name]
		p.Signal(t, syscall.SIGSTOP)
		if queryInt(t, ctx, db, `SELECT count(*) FROM ingest.job WHERE status = 'running' AND worker = $1`, name) > 0 {
			victim = name
			return true
		}
		p.Signal(t, syscall.SIGCONT)
		return false
	})
	t.Logf("killing %s after %d pages", victim, queryInt(t, ctx, db, `SELECT count(*) FROM test_counter_page`))
	children[victim].Signal(t, syscall.SIGKILL)
	children[victim].Exit(t)

	waitFor(t, 60*time.Second, "every stream at its target", func() bool {
		return queryInt(t, ctx, db, `SELECT count(*) FROM ingest.stream WHERE coalesce((cursor->>'n')::int, 0) < $1`, target) == 0 &&
			queryInt(t, ctx, db, `SELECT count(*) FROM ingest.job`) == 0
	})
	survivor := a
	if victim == "a" {
		survivor = b
	}
	survivor.Signal(t, syscall.SIGTERM)
	if code := survivor.Exit(t); code != 0 {
		t.Errorf("the survivor exited %d after SIGTERM, want 0", code)
	}

	for _, id := range streams {
		checkStream(t, ctx, db, id, target)
		if v := stream(t, ctx, db, id).Version; v != target/pageSize {
			t.Errorf("stream %d: cursor_version %d, want %d: one per page, none for a page that rolled back", id, v, target/pageSize)
		}
	}
	if n := queryInt(t, ctx, db, `SELECT count(*) FROM ingest.job_run WHERE status <> 'succeeded'`); n != 0 {
		t.Errorf("%d job_run rows did not succeed", n)
	}
	if n := queryInt(t, ctx, db, `SELECT count(*) FROM ingest.job_run WHERE attempt > 0`); n == 0 {
		t.Error("no job_run row counts an attempt: the killed worker's job was not taken over")
	}
	overlaps := queryInt(t, ctx, db, `
		SELECT count(*) FROM test_counter_page p JOIN test_counter_page q
		  ON p.lease_key = q.lease_key AND p.id < q.id AND p.started_at < q.ended_at AND q.started_at < p.ended_at`)
	if overlaps != 0 {
		t.Errorf("%d pairs of pages of one lease key overlap in time", overlaps)
	}
}

// TestStoppedWorkerHandsOver: a worker stopped with SIGTERM finishes its page, hands its job back and releases its
// lease, so another worker carries on at once instead of waiting out the 60 s TTL.
func TestStoppedWorkerHandsOver(t *testing.T) {
	ctx, dsn, db := testDB(t)
	id := addStream(t, ctx, db, counterKind, "account:1", "s", true)
	cfg := childConfig{
		DSN: dsn, Slots: 1, Poll: 100 * time.Millisecond, BudgetRequests: 1_000_000, Budget: time.Hour,
		PageSize: 1, Target: 1_000_000, Delay: 20 * time.Millisecond,
	}
	cfg.Name = "a"
	a := startChild(t, cfg)
	waitFor(t, 10*time.Second, "pages from a", func() bool {
		return queryInt(t, ctx, db, `SELECT count(*) FROM test_counter_page WHERE worker = 'a'`) >= 3
	})
	cfg.Name = "b"
	b := startChild(t, cfg)

	stopped := time.Now()
	a.Signal(t, syscall.SIGTERM)
	a.Log(t, "worker stopped")
	waitFor(t, 10*time.Second, "a page from b", func() bool {
		return queryInt(t, ctx, db, `SELECT count(*) FROM test_counter_page WHERE worker = 'b'`) > 0
	})
	var first time.Time
	if err := db.QueryRow(ctx, `SELECT min(started_at) FROM test_counter_page WHERE worker = 'b'`).Scan(&first); err != nil {
		t.Fatal(err)
	}
	t.Logf("b's first page started %s after SIGTERM", first.Sub(stopped).Round(time.Millisecond))
	if code := a.Exit(t); code != 0 {
		t.Errorf("a exited %d after SIGTERM, want 0", code)
	}

	var owner string
	var token int64
	if err := db.QueryRow(ctx, `SELECT owner, token FROM ingest.lease WHERE key = 'account:1'`).Scan(&owner, &token); err != nil {
		t.Fatal(err)
	}
	if owner != "b" || token != 2 {
		t.Errorf("lease held by %s with token %d, want b with 2", owner, token)
	}
	var attempt int
	if err := db.QueryRow(ctx, `SELECT attempt FROM ingest.job WHERE stream_id = $1`, id).Scan(&attempt); err != nil {
		t.Fatal(err)
	}
	if attempt != 0 {
		t.Errorf("attempt %d, want 0: a job handed back is not a failed attempt", attempt)
	}
	if n := queryInt(t, ctx, db, `
		SELECT count(*) FROM test_counter_page p JOIN test_counter_page q
		  ON p.worker = 'a' AND q.worker = 'b' AND q.started_at < p.ended_at`); n != 0 {
		t.Errorf("%d of b's pages started before one of a's ended", n)
	}

	b.Signal(t, syscall.SIGTERM)
	if code := b.Exit(t); code != 0 {
		t.Errorf("b exited %d after SIGTERM, want 0", code)
	}
	checkStream(t, ctx, db, id, queryInt(t, ctx, db, `SELECT (cursor->>'n')::int FROM ingest.stream WHERE id = $1`, id))
}
