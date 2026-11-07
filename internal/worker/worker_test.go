package worker

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// jobRun is the part of a job_run row these tests look at.
type jobRun struct {
	Status, Worker       string
	Requests, Items      int
	Attempt              int
	More                 bool
	ErrorClass, ErrorMsg *string
}

func lastRun(t *testing.T, ctx context.Context, db *pgxpool.Pool, streamID int64) jobRun {
	t.Helper()
	var r jobRun
	err := db.QueryRow(ctx, `
		SELECT status, worker, requests, items, attempt, more, error_class, error
		FROM ingest.job_run WHERE stream_id = $1 ORDER BY finished_at DESC LIMIT 1`, streamID).Scan(
		&r.Status, &r.Worker, &r.Requests, &r.Items, &r.Attempt, &r.More, &r.ErrorClass, &r.ErrorMsg)
	if err != nil {
		t.Fatal(err)
	}
	return r
}

// streamState is the health and schedule of a stream, with times as offsets from the database's now().
type streamState struct {
	Version       int
	DueIn         time.Duration
	OK            bool
	Failures      int
	LastErrorAt   bool
	LastErrClass  *string
	LastErrorText *string
}

func stream(t *testing.T, ctx context.Context, db *pgxpool.Pool, id int64) streamState {
	t.Helper()
	var s streamState
	err := db.QueryRow(ctx, `
		SELECT cursor_version, next_due_at - now(), last_ok_at IS NOT NULL, consecutive_failures,
		       last_error_at IS NOT NULL, last_error_class, last_error
		FROM ingest.stream WHERE id = $1`, id).Scan(
		&s.Version, &s.DueIn, &s.OK, &s.Failures, &s.LastErrorAt, &s.LastErrClass, &s.LastErrorText)
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func TestStreamJobRunsToTheEnd(t *testing.T) {
	ctx, _, db := testDB(t)
	id := addStream(t, ctx, db, counterKind, "account:1", "s", true)
	w := &Worker{DB: db, Name: "w1", Handlers: Handlers{counterKind: counter{worker: "w1", pageSize: 3, target: 10}.handle}, Slots: 1, Poll: 20 * time.Millisecond}
	stop := start(t, w)
	waitFor(t, 10*time.Second, "job_run row", func() bool {
		return queryInt(t, ctx, db, `SELECT count(*) FROM ingest.job_run`) == 1
	})
	stop()

	if r := lastRun(t, ctx, db, id); r.Status != "succeeded" || r.Worker != "w1" || r.Requests != 4 || r.Items != 10 || r.More || r.Attempt != 0 || r.ErrorClass != nil {
		t.Errorf("job_run %+v", r)
	}
	checkStream(t, ctx, db, id, 10)
	s := stream(t, ctx, db, id)
	if s.Version != 4 || !s.OK || s.Failures != 0 || s.LastErrorAt || s.DueIn < 50*time.Minute {
		t.Errorf("stream %+v: want 4 pages, ok, due again in an hour", s)
	}
	if n := queryInt(t, ctx, db, `SELECT count(*) FROM ingest.job`); n != 0 {
		t.Errorf("%d jobs left", n)
	}
	if n := queryInt(t, ctx, db, `SELECT count(*) FROM ingest.lease WHERE key = 'account:1' AND until <= now() AND owner = 'w1'`); n != 1 {
		t.Error("the lease was not released")
	}
}

func TestBudgetEndsTheJob(t *testing.T) {
	ctx, _, db := testDB(t)
	id := addStream(t, ctx, db, counterKind, "account:1", "s", true)
	w := &Worker{DB: db, Name: "w1", Handlers: Handlers{counterKind: counter{worker: "w1", pageSize: 3, target: 10}.handle},
		Slots: 1, Poll: 20 * time.Millisecond, BudgetRequests: 2}
	stop := start(t, w)
	waitFor(t, 10*time.Second, "job_run row", func() bool {
		return queryInt(t, ctx, db, `SELECT count(*) FROM ingest.job_run`) == 1
	})
	stop()

	if r := lastRun(t, ctx, db, id); r.Status != "succeeded" || r.Requests != 2 || r.Items != 6 || !r.More {
		t.Errorf("job_run %+v: want 2 requests, 6 items, more", r)
	}
	checkStream(t, ctx, db, id, 6)
	if s := stream(t, ctx, db, id); s.DueIn > 0 || !s.OK {
		t.Errorf("stream %+v: want due again at once", s)
	}
}

func TestFailedJobs(t *testing.T) {
	ctx, _, db := testDB(t)
	failing := addStream(t, ctx, db, "test.failing", "account:1", "failing", true)
	panicking := addStream(t, ctx, db, "test.panicking", "account:2", "panicking", true)
	page := counter{worker: "w1", pageSize: 3, target: 3}.handle
	handlers := Handlers{
		"test.failing": func(ctx context.Context, job *Job) (Result, error) {
			res, err := page(ctx, job) // one page commits before the failure
			if err != nil {
				return res, err
			}
			res.Requests++
			return res, errors.New("venue said no")
		},
		"test.panicking": func(context.Context, *Job) (Result, error) { panic("bug") },
	}
	stop := start(t, &Worker{DB: db, Name: "w1", Handlers: handlers, Slots: 2, Poll: 20 * time.Millisecond})
	waitFor(t, 10*time.Second, "two job_run rows", func() bool {
		return queryInt(t, ctx, db, `SELECT count(*) FROM ingest.job_run`) == 2
	})
	stop()

	r := lastRun(t, ctx, db, failing)
	if r.Status != "failed" || r.ErrorClass == nil || *r.ErrorClass != "internal" || r.ErrorMsg == nil || *r.ErrorMsg != "venue said no" ||
		r.Requests != 2 || r.Items != 3 {
		t.Errorf("failing job_run %+v", r)
	}
	checkStream(t, ctx, db, failing, 3) // the committed page stays
	s := stream(t, ctx, db, failing)
	if s.OK || s.Failures != 1 || !s.LastErrorAt || s.LastErrClass == nil || *s.LastErrClass != "internal" ||
		s.LastErrorText == nil || *s.LastErrorText != "venue said no" || s.DueIn < 50*time.Minute {
		t.Errorf("failing stream %+v", s)
	}

	r = lastRun(t, ctx, db, panicking)
	if r.Status != "failed" || r.ErrorMsg == nil || !strings.Contains(*r.ErrorMsg, "panicked: bug") {
		t.Errorf("panicking job_run %+v", r)
	}
}

func TestMovedCursorCancelsThePage(t *testing.T) {
	ctx, _, db := testDB(t)
	id := addStream(t, ctx, db, "test.raced", "account:1", "s", true)
	handlers := Handlers{"test.raced": func(ctx context.Context, job *Job) (Result, error) {
		// Another worker moves the cursor between this job's read and its first page.
		if _, err := db.Exec(ctx, `UPDATE ingest.stream SET cursor = '{"n": 5}', cursor_version = cursor_version + 1 WHERE id = $1`, job.StreamID); err != nil {
			return Result{}, err
		}
		err := job.CommitPage(ctx, func(tx pgx.Tx) error {
			_, err := tx.Exec(ctx, `INSERT INTO test_counter_item (stream_id, seq) VALUES ($1, 1)`, job.StreamID)
			return err
		}, map[string]int{"n": 1})
		return Result{Requests: 1}, err
	}}
	stop := start(t, &Worker{DB: db, Name: "w1", Handlers: handlers, Slots: 1, Poll: 20 * time.Millisecond})
	waitFor(t, 10*time.Second, "job_run row", func() bool {
		return queryInt(t, ctx, db, `SELECT count(*) FROM ingest.job_run`) == 1
	})
	stop()

	if r := lastRun(t, ctx, db, id); r.Status != "cancelled" || r.ErrorClass != nil || r.ErrorMsg == nil || !strings.Contains(*r.ErrorMsg, "cursor moved") {
		t.Errorf("job_run %+v: want cancelled, no error class", r)
	}
	if n := queryInt(t, ctx, db, `SELECT count(*) FROM test_counter_item`); n != 0 {
		t.Errorf("%d rows written by the page that lost the race, want 0", n)
	}
	if s := stream(t, ctx, db, id); s.Version != 1 || s.DueIn > 0 || s.Failures != 0 || s.LastErrorAt {
		t.Errorf("stream %+v: want the other worker's cursor, due again, no failure", s)
	}
}

func TestStopHandsTheJobBack(t *testing.T) {
	ctx, _, db := testDB(t)
	id := addStream(t, ctx, db, counterKind, "account:1", "s", true)
	w := &Worker{DB: db, Name: "w1", Handlers: Handlers{counterKind: counter{worker: "w1", pageSize: 1, target: 1_000_000, delay: 5 * time.Millisecond}.handle},
		Slots: 1, Poll: 20 * time.Millisecond, BudgetRequests: 1_000_000, BudgetTime: time.Hour}
	stop := start(t, w)
	waitFor(t, 10*time.Second, "three pages", func() bool {
		return queryInt(t, ctx, db, `SELECT count(*) FROM test_counter_item`) >= 3
	})
	stop()

	var status string
	var worker *string
	var token *int64
	var attempt int
	if err := db.QueryRow(ctx, `SELECT status, worker, lease_token, attempt FROM ingest.job WHERE stream_id = $1`, id).Scan(&status, &worker, &token, &attempt); err != nil {
		t.Fatal(err)
	}
	if status != "pending" || worker != nil || token != nil || attempt != 0 {
		t.Errorf("job after stop: %s, worker %v, token %v, attempt %d; want pending, no attempt counted", status, worker, token, attempt)
	}
	if n := queryInt(t, ctx, db, `SELECT count(*) FROM ingest.lease WHERE until > now()`); n != 0 {
		t.Error("the lease is still held after stop")
	}
	if n := queryInt(t, ctx, db, `SELECT count(*) FROM ingest.job_run`); n != 0 {
		t.Errorf("%d job_run rows: a handed-back job is not finished", n)
	}
	n := queryInt(t, ctx, db, `SELECT (cursor->>'n')::int FROM ingest.stream WHERE id = $1`, id)
	checkStream(t, ctx, db, id, n)
}
