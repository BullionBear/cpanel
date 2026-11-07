package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BullionBear/cpanel/internal/classify"
	"github.com/BullionBear/cpanel/internal/reconcile"
)

func TestBackoff(t *testing.T) {
	if got := backoff(1, 0); got != 15*time.Second {
		t.Errorf("first failure, factor 0.5: %s, want 15s", got)
	}
	if got := backoff(1, 1); got != 30*time.Second {
		t.Errorf("first failure, full: %s, want 30s", got)
	}
	if got := backoff(2, 1); got != time.Minute {
		t.Errorf("second failure: %s, want 1m", got)
	}
	if got := backoff(100, 1); got != backoffCap {
		t.Errorf("capped: %s, want %s", got, backoffCap)
	}
	if got := backoff(100, 0); got != backoffCap/2 {
		t.Errorf("capped, half: %s, want %s", got, backoffCap/2)
	}
}

func TestEffect(t *testing.T) {
	r := (&Worker{Egress: "edge"}).runner()
	eff := r.effectOf(&classify.ClassifiedError{Class: classify.RateLimited, RetryAfter: 1000 * time.Hour})
	if !eff.pushLease || eff.delay != maxDelay || eff.due != "delay" {
		t.Errorf("rate limit cap: %+v", eff)
	}
	eff = r.effectOf(&classify.ClassifiedError{Class: classify.RateLimited})
	if !eff.pushLease || !eff.backoff {
		t.Errorf("rate limit without a header: %+v", eff)
	}
	eff = (&Worker{}).runner().effectOf(&classify.ClassifiedError{Class: classify.IPBanned, RetryAfter: time.Minute})
	if eff.ban != 0 || !eff.banSkipped || eff.class == nil || *eff.class != "ip_banned" {
		t.Errorf("IP ban without an egress name: %+v", eff)
	}
	eff = r.effectOf(&classify.ClassifiedError{Class: "nope"})
	if eff.class == nil || *eff.class != "internal" || eff.pauseStream || eff.pushLease || eff.ban != 0 {
		t.Errorf("unknown class: %+v", eff)
	}
	eff = r.effectOf(&classify.ClassifiedError{Class: classify.NotFound})
	if eff.class == nil || *eff.class != "not_found" || eff.due != "period" || eff.pauseStream || eff.pushLease || eff.auth || eff.alert {
		t.Errorf("not_found has no policy: %+v", eff)
	}
	local := (&Worker{Local: []string{"other"}}).runner().local
	if !slices.Contains(local, maintPartitionsKind) || !slices.Contains(local, "other") {
		t.Fatalf("local kinds %v", local)
	}
	if reconcile.MaintPartitions.Name != maintPartitionsKind {
		t.Fatalf("reconcile registers %q, the worker treats %q as local", reconcile.MaintPartitions.Name, maintPartitionsKind)
	}
}

// secondsAhead is how many seconds the column in expr is after the database's now().
// expr is "<column> - now() FROM <table> WHERE ...".
func secondsAhead(t *testing.T, ctx context.Context, db *pgxpool.Pool, expr string, args ...any) float64 {
	t.Helper()
	head, tail, ok := strings.Cut(expr, " FROM ")
	if !ok {
		t.Fatalf("expr %q has no FROM", expr)
	}
	var s float64
	q := `SELECT EXTRACT(EPOCH FROM (` + head + `)) FROM ` + tail
	if err := db.QueryRow(ctx, q, args...).Scan(&s); err != nil {
		t.Fatalf("%s: %v", q, err)
	}
	return s
}

func near(t *testing.T, got, want float64) {
	t.Helper()
	if got < want-5 || got > want+5 {
		t.Errorf("%0.1fs, want about %0.0fs", got, want)
	}
}

// addIdleJob is a pending job of a kind this worker does not run, so it stays put and shows whether its
// not_before moved.
func addIdleJob(t *testing.T, ctx context.Context, db *pgxpool.Pool, lease, key string) {
	t.Helper()
	exec(t, ctx, db, `INSERT INTO ingest.job (kind, dedup_key, lease_key) VALUES ('test.other', $1, $2)`, key, lease)
}

func addExchange(t *testing.T, ctx context.Context, db *pgxpool.Pool, lease, key, path string) int64 {
	t.Helper()
	id := addStream(t, ctx, db, "test.exchange", lease, key, false)
	params, err := json.Marshal(map[string]string{"path": path})
	if err != nil {
		t.Fatal(err)
	}
	exec(t, ctx, db, `INSERT INTO ingest.job (kind, dedup_key, lease_key, stream_id, params) VALUES ($1, $2, $3, $4, $5)`,
		"test.exchange", fmt.Sprintf("stream:%d", id), lease, id, params)
	return id
}

// calls records hooks the worker runs on its own goroutine.
type calls struct {
	mu    sync.Mutex
	auth  []AuthFailure
	alert []Alert
	fails int
}

func (c *calls) authHook(ctx context.Context, tx pgx.Tx, ev AuthFailure) error {
	var n int
	if err := tx.QueryRow(ctx, `SELECT consecutive_failures FROM ingest.stream WHERE id = $1`, ev.StreamID).Scan(&n); err != nil {
		return err
	}
	c.mu.Lock()
	c.auth = append(c.auth, ev)
	c.fails = n
	c.mu.Unlock()
	return nil
}

func (c *calls) alertHook(_ context.Context, ev Alert) {
	c.mu.Lock()
	c.alert = append(c.alert, ev)
	c.mu.Unlock()
}

func exchangeHandler(srv *httptest.Server, hits *atomic.Int32, keepNotFound bool) Handler {
	client := srv.Client()
	return func(ctx context.Context, job *Job) (Result, error) {
		hits.Add(1)
		var p struct {
			Path string `json:"path"`
		}
		if err := json.Unmarshal(job.Params, &p); err != nil {
			return Result{}, err
		}
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, srv.URL+p.Path, nil)
		if err != nil {
			return Result{}, err
		}
		resp, err := classify.Do(ctx, client, req)
		if err != nil {
			if !keepNotFound && classify.Is(err, classify.NotFound) {
				return Result{Requests: 1}, nil
			}
			return Result{Requests: 1}, err
		}
		resp.Body.Close()
		return Result{Requests: 1, Items: 1}, nil
	}
}

func TestExchangeErrors(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/auth":
			http.Error(w, "invalid key", http.StatusUnauthorized)
		case "/rate":
			w.Header().Set("Retry-After", "120")
			http.Error(w, "slow down", http.StatusTooManyRequests)
		case "/rate-plain":
			http.Error(w, "slow down", http.StatusTooManyRequests)
		case "/ban":
			w.Header().Set("Retry-After", "120")
			http.Error(w, "banned", http.StatusTeapot)
		case "/down":
			http.Error(w, "down", http.StatusServiceUnavailable)
		case "/bug":
			http.Error(w, "bad param", http.StatusBadRequest)
		case "/gone":
			http.Error(w, "missing", http.StatusNotFound)
		default:
			w.WriteHeader(http.StatusOK)
		}
	}))
	t.Cleanup(srv.Close)

	newWorker := func(db *pgxpool.Pool, name, egress string, hits *atomic.Int32, keepNotFound bool, c *calls) *Worker {
		return &Worker{
			DB: db, Name: name, Egress: egress, Slots: 2, Poll: 20 * time.Millisecond,
			Rand: func() float64 { return 1 }, CredentialHook: c.authHook, AlertHook: c.alertHook,
			Handlers: Handlers{
				"test.exchange":     exchangeHandler(srv, hits, keepNotFound),
				maintPartitionsKind: MaintPartitions,
			},
		}
	}

	t.Run("auth calls the credential hook", func(t *testing.T) {
		ctx, _, db := testDB(t)
		id := addExchange(t, ctx, db, "account:1", "auth", "/auth")
		addIdleJob(t, ctx, db, "account:2", "other")
		var c calls
		var hits atomic.Int32
		stop := start(t, newWorker(db, "w", "edge-1", &hits, false, &c))
		waitFor(t, 10*time.Second, "auth job_run", func() bool {
			return queryInt(t, ctx, db, `SELECT count(*) FROM ingest.job_run WHERE stream_id = $1`, id) == 1
		})
		stop()
		r := lastRun(t, ctx, db, id)
		if r.Status != "failed" || r.ErrorClass == nil || *r.ErrorClass != "auth" || r.ErrorMsg == nil || *r.ErrorMsg != "auth: HTTP 401: invalid key" {
			t.Errorf("job_run %+v", r)
		}
		c.mu.Lock()
		defer c.mu.Unlock()
		if len(c.auth) != 1 || c.auth[0].StreamID != id || c.auth[0].LeaseKey != "account:1" || c.fails != 1 {
			t.Errorf("hook %+v, failures %d", c.auth, c.fails)
		}
		if len(c.alert) != 0 {
			t.Errorf("alerted: %+v", c.alert)
		}
		s := stream(t, ctx, db, id)
		if s.Failures != 1 || s.LastErrClass == nil || *s.LastErrClass != "auth" || s.DueIn < 50*time.Minute || s.DueIn > 70*time.Minute {
			t.Errorf("stream %+v", s)
		}
		if paused := queryInt(t, ctx, db, `SELECT count(*) FROM ingest.stream WHERE id = $1 AND paused_reason IS NOT NULL`, id); paused != 0 {
			t.Error("auth paused the stream")
		}
		if n := queryInt(t, ctx, db, `SELECT count(*) FROM ingest.pause`); n != 0 {
			t.Error("auth wrote a pause")
		}
		if ahead := secondsAhead(t, ctx, db, `not_before - now() FROM ingest.job WHERE dedup_key = 'other'`); ahead > 5 {
			t.Errorf("another lease was pushed %0.1fs", ahead)
		}
	})

	t.Run("429 with Retry-After pushes the lease and does not ban", func(t *testing.T) {
		ctx, _, db := testDB(t)
		id := addExchange(t, ctx, db, "account:1", "rate", "/rate")
		sib := addStream(t, ctx, db, "test.exchange", "account:1", "sib", false)
		addIdleJob(t, ctx, db, "account:1", "sib-job")
		addIdleJob(t, ctx, db, "account:2", "other")
		var c calls
		var hits atomic.Int32
		stop := start(t, newWorker(db, "w", "edge-1", &hits, false, &c))
		waitFor(t, 10*time.Second, "rate job_run", func() bool {
			return queryInt(t, ctx, db, `SELECT count(*) FROM ingest.job_run WHERE stream_id = $1`, id) == 1
		})
		stop()
		if r := lastRun(t, ctx, db, id); r.ErrorClass == nil || *r.ErrorClass != "rate_limited" {
			t.Errorf("job_run %+v", r)
		}
		near(t, secondsAhead(t, ctx, db, `next_due_at - now() FROM ingest.stream WHERE id = $1`, id), 120)
		near(t, secondsAhead(t, ctx, db, `next_due_at - now() FROM ingest.stream WHERE id = $1`, sib), 120)
		near(t, secondsAhead(t, ctx, db, `not_before - now() FROM ingest.job WHERE dedup_key = 'sib-job'`), 120)
		if ahead := secondsAhead(t, ctx, db, `not_before - now() FROM ingest.job WHERE dedup_key = 'other'`); ahead > 5 {
			t.Errorf("another lease was pushed %0.1fs", ahead)
		}
		if n := queryInt(t, ctx, db, `SELECT count(*) FROM ingest.pause`); n != 0 {
			t.Error("a rate limit wrote an egress pause")
		}
		if paused := queryInt(t, ctx, db, `SELECT count(*) FROM ingest.stream WHERE paused_reason IS NOT NULL`); paused != 0 {
			t.Error("a rate limit paused a stream")
		}
	})

	t.Run("429 without Retry-After backs off", func(t *testing.T) {
		ctx, _, db := testDB(t)
		id := addExchange(t, ctx, db, "account:1", "rate", "/rate-plain")
		var hits atomic.Int32
		w := newWorker(db, "w", "edge-1", &hits, false, &calls{})
		w.Rand = func() float64 { return 0 }
		stop := start(t, w)
		waitFor(t, 10*time.Second, "rate job_run", func() bool {
			return queryInt(t, ctx, db, `SELECT count(*) FROM ingest.job_run`) == 1
		})
		stop()
		near(t, secondsAhead(t, ctx, db, `next_due_at - now() FROM ingest.stream WHERE id = $1`, id), 15)
	})

	t.Run("503 backs off only that stream and then doubles", func(t *testing.T) {
		ctx, _, db := testDB(t)
		id := addExchange(t, ctx, db, "account:1", "down", "/down")
		addIdleJob(t, ctx, db, "account:1", "sib-job")
		var hits atomic.Int32
		stop := start(t, newWorker(db, "w", "edge-1", &hits, false, &calls{}))
		waitFor(t, 10*time.Second, "first failure", func() bool {
			return queryInt(t, ctx, db, `SELECT count(*) FROM ingest.job_run WHERE stream_id = $1`, id) == 1
		})
		if r := lastRun(t, ctx, db, id); r.ErrorClass == nil || *r.ErrorClass != "unavailable" {
			t.Errorf("job_run %+v", r)
		}
		near(t, secondsAhead(t, ctx, db, `next_due_at - now() FROM ingest.stream WHERE id = $1`, id), 30)
		if ahead := secondsAhead(t, ctx, db, `not_before - now() FROM ingest.job WHERE dedup_key = 'sib-job'`); ahead > 5 {
			t.Errorf("unavailable pushed a sibling job %0.1fs", ahead)
		}
		exec(t, ctx, db, `UPDATE ingest.stream SET next_due_at = now() WHERE id = $1`, id)
		exec(t, ctx, db, `INSERT INTO ingest.job (kind, dedup_key, lease_key, stream_id, params) VALUES ('test.exchange', $1, 'account:1', $2, '{"path":"/down"}')`,
			fmt.Sprintf("stream:%d:2", id), id)
		waitFor(t, 10*time.Second, "second failure", func() bool {
			return queryInt(t, ctx, db, `SELECT count(*) FROM ingest.job_run WHERE stream_id = $1`, id) == 2
		})
		stop()
		if s := stream(t, ctx, db, id); s.Failures != 2 {
			t.Errorf("failures %d, want 2", s.Failures)
		}
		near(t, secondsAhead(t, ctx, db, `next_due_at - now() FROM ingest.stream WHERE id = $1`, id), 60)
	})

	t.Run("418 pauses the egress and nothing else", func(t *testing.T) {
		ctx, _, db := testDB(t)
		banned := addExchange(t, ctx, db, "account:1", "ban", "/ban")
		var hits atomic.Int32
		var c calls
		a := start(t, newWorker(db, "a", "edge-1", &hits, false, &c))
		waitFor(t, 10*time.Second, "ban job_run", func() bool {
			return queryInt(t, ctx, db, `SELECT count(*) FROM ingest.job_run WHERE stream_id = $1`, banned) == 1
		})
		a()
		if r := lastRun(t, ctx, db, banned); r.ErrorClass == nil || *r.ErrorClass != "ip_banned" {
			t.Errorf("job_run %+v", r)
		}
		var scope, reason string
		var left float64
		if err := db.QueryRow(ctx, `SELECT scope, reason, EXTRACT(EPOCH FROM (until - now())) FROM ingest.pause`).Scan(&scope, &reason, &left); err != nil {
			t.Fatal(err)
		}
		if scope != EgressScope("edge-1") || reason != "ip_banned: HTTP 418: banned" {
			t.Errorf("pause %s %q", scope, reason)
		}
		near(t, left, 120)
		if ahead := secondsAhead(t, ctx, db, `next_due_at - now() FROM ingest.stream WHERE id = $1`, banned); ahead > 5 {
			t.Errorf("the banned stream was delayed %0.1fs; the pause is what holds it", ahead)
		}

		outbound := addExchange(t, ctx, db, "account:2", "later", "/ok")
		exec(t, ctx, db, `INSERT INTO ingest.job (kind, dedup_key, lease_key) VALUES ('maint.partitions', 'periodic:maint', 'global:maint')`)
		hits.Store(0)
		start(t, newWorker(db, "b", "edge-1", &hits, false, &c))
		waitFor(t, 10*time.Second, "local maintenance during the pause", func() bool {
			return queryInt(t, ctx, db, `SELECT count(*) FROM ingest.job_run WHERE kind = 'maint.partitions' AND status = 'succeeded'`) == 1
		})
		time.Sleep(300 * time.Millisecond)
		var status string
		if err := db.QueryRow(ctx, `SELECT status FROM ingest.job WHERE stream_id = $1`, outbound).Scan(&status); err != nil {
			t.Fatal(err)
		}
		if status != "pending" || hits.Load() != 0 {
			t.Fatalf("same egress claimed the outbound job: status %s, requests %d", status, hits.Load())
		}

		start(t, newWorker(db, "c", "edge-2", &hits, false, &c))
		waitFor(t, 10*time.Second, "the other egress taking the job", func() bool {
			return queryInt(t, ctx, db, `SELECT count(*) FROM ingest.job_run WHERE stream_id = $1 AND worker = 'c'`, outbound) == 1
		})
	})

	t.Run("an IP ban with no egress name is only recorded", func(t *testing.T) {
		ctx, _, db := testDB(t)
		id := addStream(t, ctx, db, "test.exchange", "account:1", "ban", true)
		var hits atomic.Int32
		w := newWorker(db, "w", "", &hits, false, &calls{})
		w.Handlers = Handlers{"test.exchange": func(context.Context, *Job) (Result, error) {
			return Result{Requests: 1}, &classify.ClassifiedError{Class: classify.IPBanned, Status: 418, RetryAfter: time.Minute}
		}}
		stop := start(t, w)
		waitFor(t, 10*time.Second, "job_run", func() bool {
			return queryInt(t, ctx, db, `SELECT count(*) FROM ingest.job_run WHERE stream_id = $1`, id) == 1
		})
		stop()
		if r := lastRun(t, ctx, db, id); r.ErrorClass == nil || *r.ErrorClass != "ip_banned" {
			t.Errorf("job_run %+v", r)
		}
		if n := queryInt(t, ctx, db, `SELECT count(*) FROM ingest.pause`); n != 0 {
			t.Error("wrote a pause without an egress name")
		}
	})

	t.Run("bad request pauses the stream", func(t *testing.T) {
		ctx, _, db := testDB(t)
		id := addExchange(t, ctx, db, "account:1", "bug", "/bug")
		var c calls
		var hits atomic.Int32
		stop := start(t, newWorker(db, "w", "edge-1", &hits, false, &c))
		waitFor(t, 10*time.Second, "bad request job_run", func() bool {
			return queryInt(t, ctx, db, `SELECT count(*) FROM ingest.job_run`) == 1
		})
		stop()
		if r := lastRun(t, ctx, db, id); r.ErrorClass == nil || *r.ErrorClass != "bad_request" {
			t.Errorf("job_run %+v", r)
		}
		var reason string
		var at *time.Time
		if err := db.QueryRow(ctx, `SELECT paused_reason, paused_at FROM ingest.stream WHERE id = $1`, id).Scan(&reason, &at); err != nil {
			t.Fatal(err)
		}
		if reason != "bad_request" || at == nil {
			t.Errorf("paused %q at %v", reason, at)
		}
		c.mu.Lock()
		if len(c.alert) != 1 || c.alert[0].StreamID != id || c.alert[0].Class != "bad_request" || len(c.auth) != 0 {
			t.Errorf("alert %+v, auth %+v", c.alert, c.auth)
		}
		c.mu.Unlock()
		res := (&reconcile.Loop{DB: db, Rules: []reconcile.Rule{reconcile.Streams}}).Round(ctx)
		if res.Err != nil || len(res.Rules) != 1 || res.Rules[0].Inserted != 0 {
			t.Fatalf("reconcile after the pause: %+v", res)
		}
		if n := queryInt(t, ctx, db, `SELECT count(*) FROM ingest.job`); n != 0 {
			t.Errorf("%d jobs dispatched for a paused stream", n)
		}
	})

	t.Run("not found is the handler's decision", func(t *testing.T) {
		ctx, _, db := testDB(t)
		id := addExchange(t, ctx, db, "account:1", "gone", "/gone")
		var c calls
		var hits atomic.Int32
		stop := start(t, newWorker(db, "w", "edge-1", &hits, false, &c))
		waitFor(t, 10*time.Second, "not found handled", func() bool {
			return queryInt(t, ctx, db, `SELECT count(*) FROM ingest.job_run WHERE stream_id = $1`, id) == 1
		})
		stop()
		r := lastRun(t, ctx, db, id)
		if r.Status != "succeeded" || r.ErrorClass != nil || r.Requests != 1 {
			t.Errorf("job_run %+v: the handler treated not_found as done", r)
		}
		s := stream(t, ctx, db, id)
		if !s.OK || s.Failures != 0 {
			t.Errorf("stream %+v", s)
		}
		c.mu.Lock()
		defer c.mu.Unlock()
		if len(c.auth) != 0 || len(c.alert) != 0 {
			t.Errorf("hooks ran: auth %+v alert %+v", c.auth, c.alert)
		}
		if n := queryInt(t, ctx, db, `SELECT count(*) FROM ingest.pause`); n != 0 {
			t.Error("not_found wrote a pause")
		}
	})

	t.Run("a returned not_found has no backoff", func(t *testing.T) {
		ctx, _, db := testDB(t)
		id := addExchange(t, ctx, db, "account:1", "gone", "/gone")
		addIdleJob(t, ctx, db, "account:1", "sib-job")
		var hits atomic.Int32
		stop := start(t, newWorker(db, "w", "edge-1", &hits, true, &calls{}))
		waitFor(t, 10*time.Second, "not found job_run", func() bool {
			return queryInt(t, ctx, db, `SELECT count(*) FROM ingest.job_run WHERE stream_id = $1`, id) == 1
		})
		stop()
		if r := lastRun(t, ctx, db, id); r.Status != "failed" || r.ErrorClass == nil || *r.ErrorClass != "not_found" {
			t.Errorf("job_run %+v", r)
		}
		s := stream(t, ctx, db, id)
		if s.Failures != 1 || s.DueIn < 50*time.Minute {
			t.Errorf("stream %+v: want one failure and the normal period", s)
		}
		if paused := queryInt(t, ctx, db, `SELECT count(*) FROM ingest.stream WHERE paused_reason IS NOT NULL`); paused != 0 {
			t.Error("not_found paused a stream")
		}
		if ahead := secondsAhead(t, ctx, db, `not_before - now() FROM ingest.job WHERE dedup_key = 'sib-job'`); ahead > 5 {
			t.Errorf("not_found pushed a sibling %0.1fs", ahead)
		}
	})
}

func TestCredentialHookFailureRollsBack(t *testing.T) {
	ctx, _, db := testDB(t)
	id := addStream(t, ctx, db, "test.exchange", "account:1", "auth", true)
	w := &Worker{
		DB: db, Name: "w", Egress: "edge-1", Slots: 1, Poll: 20 * time.Millisecond,
		Handlers: Handlers{"test.exchange": func(context.Context, *Job) (Result, error) {
			return Result{Requests: 1}, &classify.ClassifiedError{Class: classify.Auth, Status: 401}
		}},
		CredentialHook: func(context.Context, pgx.Tx, AuthFailure) error { return errors.New("hook down") },
	}
	stop := start(t, w)
	waitFor(t, 10*time.Second, "the job to be claimed", func() bool {
		return queryInt(t, ctx, db, `SELECT count(*) FROM ingest.job WHERE stream_id = $1 AND (status = 'running' OR attempt > 0)`, id) == 1
	})
	time.Sleep(200 * time.Millisecond)
	stop()
	if n := queryInt(t, ctx, db, `SELECT count(*) FROM ingest.job_run`); n != 0 {
		t.Errorf("%d job_run rows: the hook's error did not roll the failure back", n)
	}
	var status string
	if err := db.QueryRow(ctx, `SELECT status FROM ingest.job WHERE stream_id = $1`, id).Scan(&status); err != nil {
		t.Fatal(err)
	}
	if status != "pending" && status != "running" {
		t.Errorf("job status %s", status)
	}
}
