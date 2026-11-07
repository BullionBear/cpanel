package worker

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
)

// Handler runs one job. It makes outbound requests while job.Continue allows, writes each page with
// job.CommitPage, and returns what it did. A returned error fails the job; C3-04 classifies it.
type Handler func(ctx context.Context, job *Job) (Result, error)

// Result is what a handler did.
type Result struct {
	Requests int  // outbound requests made
	Items    int  // rows written
	More     bool // the stream has not caught up: dispatch it again at once
}

// Budget bounds one job: at most Requests outbound requests, and no new page after Deadline.
type Budget struct {
	Requests int
	Deadline time.Time
}

// ErrCursorMoved is CommitPage's error when the stream's cursor moved since this job read it: another worker ran
// the stream. The page rolled back. The handler must return; the job ends cancelled and the stream is due again.
var ErrCursorMoved = errors.New("worker: the stream's cursor moved under this job")

// Job is one claimed job, as its handler sees it.
type Job struct {
	ID       int64
	Kind     string
	LeaseKey string
	StreamID int64  // 0 for a periodic job
	Periodic string // "" for a stream job
	Params   json.RawMessage
	Attempt  int             // how many times a holder of the job died or lost its lease
	Cursor   json.RawMessage // the stream's cursor, read when the job started and moved by CommitPage; nil if unset
	Budget   Budget

	r        *runner
	lease    *lease
	renew    time.Duration
	stopping context.Context // ends when the worker is told to stop
	version  int64           // the stream's cursor_version that Cursor goes with
}

// Continue reports whether the handler may start another page after making requests requests: the budget has
// room, the lease is held and the worker is not stopping. When it is false the handler returns, with More set if
// the stream has not caught up.
func (j *Job) Continue(requests int) bool {
	return requests < j.Budget.Requests && time.Now().Before(j.Budget.Deadline) &&
		j.stopping.Err() == nil && !j.lease.lost(j.renew)
}

// CommitPage writes one page: in one transaction it moves the stream's cursor to cursor, if nobody moved it since
// this job read it, and runs write. It returns ErrCursorMoved, and writes nothing, when the cursor moved. A nil
// cursor keeps the cursor as it is; a periodic job has no stream, so CommitPage only runs write.
func (j *Job) CommitPage(ctx context.Context, write func(pgx.Tx) error, cursor any) error {
	var next []byte
	if cursor != nil {
		var err error
		if next, err = json.Marshal(cursor); err != nil {
			return err
		}
	}
	err := pgx.BeginFunc(ctx, j.r.db, func(tx pgx.Tx) error {
		if j.StreamID != 0 {
			tag, err := tx.Exec(ctx, `
				UPDATE ingest.stream SET cursor = coalesce($1::jsonb, cursor), cursor_version = cursor_version + 1
				WHERE id = $2 AND cursor_version = $3`,
				next, j.StreamID, j.version)
			if err != nil {
				return err
			}
			if tag.RowsAffected() == 0 {
				return ErrCursorMoved
			}
		}
		if write == nil {
			return nil
		}
		return write(tx)
	})
	if err != nil {
		return err
	}
	if j.StreamID != 0 {
		j.version++
		if next != nil {
			j.Cursor = next
		}
	}
	return nil
}

// claim marks the most urgent due pending job of l's key running under l's token, and reads its stream's cursor.
// It returns nil when the key has none or the lease is no longer ours.
func (r *runner) claim(ctx context.Context, l *lease) (*Job, error) {
	j := &Job{r: r}
	err := r.db.QueryRow(ctx, `
		WITH next AS (
			SELECT id
			FROM ingest.job
			WHERE lease_key = $1 AND status = 'pending' AND not_before <= now() AND kind = ANY($4)
			ORDER BY priority, not_before, id
			LIMIT 1
			FOR UPDATE SKIP LOCKED
		)
		UPDATE ingest.job j
		SET status = 'running', started_at = now(), worker = $2, lease_token = $3
		FROM next
		WHERE j.id = next.id
		  AND EXISTS (SELECT FROM ingest.lease l WHERE l.key = $1 AND l.owner = $2 AND l.token = $3 AND l.until > now())
		RETURNING j.id, j.kind, j.lease_key, coalesce(j.stream_id, 0), coalesce(j.periodic_name, ''), j.params, j.attempt,
		          (SELECT s.cursor FROM ingest.stream s WHERE s.id = j.stream_id),
		          coalesce((SELECT s.cursor_version FROM ingest.stream s WHERE s.id = j.stream_id), 0)`,
		l.key, r.name, l.token, r.kinds).Scan(
		&j.ID, &j.Kind, &j.LeaseKey, &j.StreamID, &j.Periodic, &j.Params, &j.Attempt, &j.Cursor, &j.version)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return j, nil
}

// maxErrorLen bounds the error text kept in job_run and on the stream.
const maxErrorLen = 2000

// complete moves j from ingest.job to ingest.job_run and updates its stream, in one transaction. It returns false,
// and writes nothing, when the job is no longer running under l: another worker took it over.
func (r *runner) complete(ctx context.Context, l *lease, j *Job, res Result, runErr error) (bool, error) {
	status := "succeeded"
	var class, msg *string
	if runErr != nil {
		text := runErr.Error()
		if len(text) > maxErrorLen {
			text = text[:maxErrorLen]
		}
		msg = &text
		if errors.Is(runErr, ErrCursorMoved) {
			status = "cancelled"
		} else {
			status = "failed"
			internal := "internal"
			class = &internal
		}
	}
	ours := false
	err := pgx.BeginFunc(ctx, r.db, func(tx pgx.Tx) error {
		tag, err := tx.Exec(ctx, `
			WITH done AS (
				DELETE FROM ingest.job
				WHERE id = $1 AND status = 'running' AND worker = $2 AND lease_token = $3
				RETURNING *
			)
			INSERT INTO ingest.job_run (id, kind, dedup_key, lease_key, stream_id, periodic_name, priority, attempt,
			                            params, enqueued_at, started_at, finished_at, worker, status, requests, items,
			                            more, error_class, error)
			SELECT id, kind, dedup_key, lease_key, stream_id, periodic_name, priority, attempt,
			       params, enqueued_at, started_at, now(), worker, $4, $5, $6, $7, $8, $9
			FROM done`,
			j.ID, r.name, l.token, status, res.Requests, res.Items, res.More, class, msg)
		if err != nil || tag.RowsAffected() == 0 {
			return err
		}
		ours = true
		if j.StreamID == 0 {
			return nil
		}
		// More, or a cursor moved by someone else, means the stream is due again now.
		again := status == "cancelled" || (status == "succeeded" && res.More)
		_, err = tx.Exec(ctx, `
			UPDATE ingest.stream SET
				next_due_at          = CASE WHEN $2 THEN now() ELSE now() + period END,
				last_ok_at           = CASE WHEN $3 = 'succeeded' THEN now() ELSE last_ok_at END,
				consecutive_failures = CASE $3 WHEN 'succeeded' THEN 0
				                               WHEN 'failed' THEN consecutive_failures + 1
				                               ELSE consecutive_failures END,
				last_error_at        = CASE WHEN $3 = 'failed' THEN now() ELSE last_error_at END,
				last_error_class     = CASE WHEN $3 = 'failed' THEN $4::text ELSE last_error_class END,
				last_error           = CASE WHEN $3 = 'failed' THEN $5::text ELSE last_error END
			WHERE id = $1`,
			j.StreamID, again, status, class, msg)
		return err
	})
	return ours, err
}

// handBack sets j back to pending, without counting an attempt, for the next holder of the key.
func (r *runner) handBack(ctx context.Context, l *lease, j *Job) error {
	_, err := r.db.Exec(ctx, `
		UPDATE ingest.job SET status = 'pending', started_at = NULL, worker = NULL, lease_token = NULL
		WHERE id = $1 AND status = 'running' AND worker = $2 AND lease_token = $3`,
		j.ID, r.name, l.token)
	return err
}
