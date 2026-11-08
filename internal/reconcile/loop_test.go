package reconcile

import (
	"context"
	"errors"
	"fmt"
	"maps"
	"slices"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

func TestRoundRunsTheRulesInOrder(t *testing.T) {
	ctx, _, conn := testDB(t)
	var observed []Result
	l := &Loop{DB: conn, Rules: Rules(), Now: func() time.Time { return t0 }, Observe: func(r Result) { observed = append(observed, r) }}

	// An empty database: the first round registers maint.partitions and dispatches it.
	res := l.Round(ctx)
	if res.Err != nil {
		t.Fatal(res.Err)
	}
	var got []string
	for _, r := range res.Rules {
		got = append(got, fmt.Sprintf("%s=%d", r.Name, r.Inserted))
	}
	if want := []string{"maint.partitions=1", "mftiko.sync=1", "streams=0", "periodics=2"}; !slices.Equal(got, want) {
		t.Errorf("rules %v, want %v", got, want)
	}
	if !res.At.Equal(t0) || len(observed) != 1 {
		t.Errorf("At %s, observed %d rounds", res.At, len(observed))
	}
	if k := keys(jobs(t, ctx, conn)); !slices.Equal(k, []string{"periodic:maint.partitions", "periodic:mftiko.sync"}) {
		t.Errorf("jobs %v", k)
	}
}

func TestFailedRoundRollsBack(t *testing.T) {
	ctx, _, conn := testDB(t)
	addStream(t, ctx, conn, "due", t0.Add(-time.Minute), "1 minute", false)
	boom := errors.New("boom")
	failing := Rule{Name: "failing", Run: func(context.Context, pgx.Tx, time.Time) (int, error) { return 0, boom }}
	var observed Result
	l := &Loop{DB: conn, Rules: []Rule{Streams, failing}, Now: func() time.Time { return t0 }, Observe: func(r Result) { observed = r }}

	res := l.Round(ctx)
	if !errors.Is(res.Err, boom) || !strings.Contains(res.Err.Error(), "rule failing") {
		t.Fatalf("Err = %v, want boom from rule failing", res.Err)
	}
	if len(res.Rules) != 2 || res.Rules[0].Inserted != 0 {
		t.Errorf("rules %+v: streams' insert was rolled back, so it reports 0", res.Rules)
	}
	if !errors.Is(observed.Err, boom) {
		t.Errorf("observed %+v", observed)
	}
	if n := len(jobs(t, ctx, conn)); n != 0 {
		t.Errorf("%d jobs after a rolled-back round, want 0", n)
	}
}

func TestRunStopsWhenItsContextEnds(t *testing.T) {
	ctx, dsn, _ := testDB(t)
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	rounds := make(chan Result, 16)
	l := &Loop{DB: pool, Rules: Rules(), Interval: 10 * time.Millisecond, Observe: func(r Result) {
		select {
		case rounds <- r:
		default:
		}
	}}
	runCtx, cancel := context.WithCancel(ctx)
	done := make(chan struct{})
	go func() { defer close(done); l.Run(runCtx) }()
	for i := range 3 {
		select {
		case r := <-rounds:
			if r.Err != nil {
				t.Fatalf("round %d: %v", i, r.Err)
			}
		case <-time.After(10 * time.Second):
			t.Fatalf("round %d did not come", i)
		}
	}
	cancel()
	select {
	case <-done:
	case <-time.After(10 * time.Second):
		t.Fatal("Run did not return after its context ended")
	}
}

// finished is one job that ran: its dedup_key and the round that finished it.
type finished struct {
	Key string
	At  time.Time
}

// The acceptance test of C3-02 (#65): three loops running the same 100 rounds side by side make exactly the jobs
// one loop makes. Between rounds a fake worker finishes jobs the way C3-03's will, so every round has new jobs to
// race on.
func TestThreeLoopsMakeTheSameJobsAsOne(t *testing.T) {
	one := simulate(t, 1)
	three := simulate(t, 3)

	count := map[string]int{}
	for _, f := range one {
		count[f.Key]++
	}
	want := map[string]int{
		"stream:1":                  100, // due every round
		"stream:2":                  20,  // its job is finished only every fifth round
		"stream:3":                  7,   // first due at minute 30, then every 10 minutes
		"periodic:maint.partitions": 2,   // minutes 0 and 60
		"periodic:mftiko.sync":      100, // every minute
		"periodic:tick":             10,  // every 10 minutes
	}
	if !maps.Equal(count, want) {
		t.Fatalf("one loop made %v, want %v", count, want)
	}
	if !slices.Equal(one, three) {
		t.Fatalf("three loops made %d jobs, one loop %d; they differ", len(three), len(one))
	}
}

// simulate runs loops loops for 100 one-minute rounds from t0 and returns the jobs they made.
func simulate(t *testing.T, loops int) []finished {
	t.Helper()
	ctx, dsn, conn := testDB(t)
	addStream(t, ctx, conn, "fast", t0, "1 minute", false)
	addStream(t, ctx, conn, "slow", t0, "1 minute", false)
	addStream(t, ctx, conn, "later", t0.Add(30*time.Minute), "10 minutes", false)
	addStream(t, ctx, conn, "paused", t0, "1 minute", true)
	exec(t, ctx, conn, `
		INSERT INTO ingest.periodic (name, kind, lease_key, period, enabled, next_due_at)
		VALUES ('tick', 'test.tick', 'global:test', '10 minutes', true, $1), ('off', 'test.off', 'global:test', '1 minute', false, $1)`, t0)

	var clock atomic.Int64
	ls := make([]*Loop, loops)
	for i := range ls {
		pool, err := pgxpool.New(ctx, dsn)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(pool.Close)
		ls[i] = &Loop{DB: pool, Rules: Rules(), Now: func() time.Time { return time.Unix(0, clock.Load()).UTC() }}
	}

	for r := range 100 {
		now := t0.Add(time.Duration(r) * time.Minute)
		clock.Store(now.UnixNano())
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
				t.Fatalf("round %d, loop %d: %v", r, i, res.Err)
			}
		}
		keep := []string{}
		if r%5 != 4 {
			keep = append(keep, "stream:2")
		}
		finish(t, ctx, conn, now, keep)
	}

	if n := len(jobs(t, ctx, conn)); n != 0 {
		t.Fatalf("%d jobs left unfinished", n)
	}
	rows, err := conn.Query(ctx, `SELECT dedup_key, finished_at FROM ingest.job_run ORDER BY finished_at, dedup_key`)
	if err != nil {
		t.Fatal(err)
	}
	out, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (finished, error) {
		var f finished
		err := row.Scan(&f.Key, &f.At)
		f.At = f.At.UTC()
		return f, err
	})
	if err != nil {
		t.Fatal(err)
	}
	return out
}

// finish is a fake worker: it ends every unfinished job except those in keep, at now, and schedules each stream it
// ran again one period later.
func finish(t *testing.T, ctx context.Context, conn *pgx.Conn, now time.Time, keep []string) {
	t.Helper()
	exec(t, ctx, conn, `
		WITH done AS (
			DELETE FROM ingest.job WHERE dedup_key <> ALL($2::text[]) RETURNING *
		), ran AS (
			INSERT INTO ingest.job_run (id, kind, dedup_key, lease_key, stream_id, periodic_name, priority, attempt,
			                            params, enqueued_at, finished_at, status)
			SELECT id, kind, dedup_key, lease_key, stream_id, periodic_name, priority, attempt,
			       params, enqueued_at, $1::timestamptz, 'succeeded'
			FROM done
			RETURNING stream_id
		)
		UPDATE ingest.stream s SET next_due_at = $1::timestamptz + s.period FROM ran WHERE s.id = ran.stream_id`,
		now, keep)
}
