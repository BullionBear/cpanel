package api

import (
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"

	cpanelv1 "github.com/BullionBear/cpanel/contract/gen/cpanel/v1"
)

// Ops reads (C3-05). Status is derived: a paused_reason is paused, consecutive
// failures are backing_off, and anything else is ok. Job error-class filters
// use the stream the job belongs to.

const streamSelect = `
	SELECT id, kind, lease_key, key,
		CASE WHEN paused_reason IS NOT NULL THEN 'paused'
		     WHEN consecutive_failures > 0 THEN 'backing_off'
		     ELSE 'ok' END,
		paused_reason, paused_at, next_due_at, last_ok_at, consecutive_failures,
		last_error_class::text, last_error, created_at
	FROM ingest.stream`

func (s *server) GetMirrorStatus(ctx context.Context, _ cpanelv1.GetMirrorStatusRequestObject) (cpanelv1.GetMirrorStatusResponseObject, error) {
	if resp, ok := mirrorGate(s.gate(ctx, false)); !ok {
		return resp, nil
	}
	rows, err := s.pool.Query(ctx, `
		SELECT s.source, s.last_attempt_at, s.last_ok_at, s.last_complete_at, s.items, s.unavailable_regions,
		       r.error_class::text, r.error
		FROM mirror.sync_state s
		LEFT JOIN LATERAL (
			SELECT error_class, error
			FROM ingest.job_run
			WHERE kind = CASE
				WHEN s.source LIKE 'mftiko.%' THEN 'mftiko.sync'
				WHEN s.source = 'sym' OR s.source LIKE 'sym:%' THEN 'sym.sync'
			END
			ORDER BY finished_at DESC, id DESC
			LIMIT 1
		) r ON true
		ORDER BY s.source`)
	if err != nil {
		return cpanelv1.GetMirrorStatusdefaultApplicationProblemPlusJSONResponse{Body: s.opsProblem(err), StatusCode: http.StatusServiceUnavailable}, nil
	}
	defer rows.Close()
	out := cpanelv1.MirrorStatus{Sources: []cpanelv1.MirrorSource{}}
	for rows.Next() {
		var src cpanelv1.MirrorSource
		if err := rows.Scan(&src.Source, &src.LastAttemptAt, &src.LastOkAt, &src.LastCompleteAt, &src.Items, &src.UnavailableRegions,
			&src.LastErrorClass, &src.LastError); err != nil {
			return cpanelv1.GetMirrorStatusdefaultApplicationProblemPlusJSONResponse{Body: s.opsProblem(err), StatusCode: http.StatusServiceUnavailable}, nil
		}
		if src.UnavailableRegions == nil {
			src.UnavailableRegions = []string{}
		}
		out.Sources = append(out.Sources, src)
	}
	if err := rows.Err(); err != nil {
		return cpanelv1.GetMirrorStatusdefaultApplicationProblemPlusJSONResponse{Body: s.opsProblem(err), StatusCode: http.StatusServiceUnavailable}, nil
	}
	return cpanelv1.GetMirrorStatus200JSONResponse(out), nil
}

func (s *server) ListStreams(ctx context.Context, req cpanelv1.ListStreamsRequestObject) (cpanelv1.ListStreamsResponseObject, error) {
	if resp, ok := streamGate(s.gate(ctx, false)); !ok {
		return resp, nil
	}
	rows, err := s.pool.Query(ctx, streamSelect+`
		WHERE ($1::text IS NULL OR kind = $1)
		  AND ($2::text IS NULL OR lease_key = $2)
		  AND ($3::text IS NULL OR (
		        CASE WHEN paused_reason IS NOT NULL THEN 'paused'
		             WHEN consecutive_failures > 0 THEN 'backing_off'
		             ELSE 'ok' END) = $3)
		  AND ($4::text IS NULL OR last_error_class::text = $4)
		ORDER BY id
		LIMIT $5`,
		textArg(req.Params.Kind), textArg(req.Params.LeaseKey), textArg(req.Params.Status), textArg(req.Params.ErrorClass),
		bounded(req.Params.Limit, 200, 500))
	if err != nil {
		return cpanelv1.ListStreamsdefaultApplicationProblemPlusJSONResponse{Body: s.opsProblem(err), StatusCode: http.StatusServiceUnavailable}, nil
	}
	defer rows.Close()
	out := make(cpanelv1.ListStreams200JSONResponse, 0)
	for rows.Next() {
		stream, err := scanStream(rows)
		if err != nil {
			return cpanelv1.ListStreamsdefaultApplicationProblemPlusJSONResponse{Body: s.opsProblem(err), StatusCode: http.StatusServiceUnavailable}, nil
		}
		out = append(out, stream)
	}
	if err := rows.Err(); err != nil {
		return cpanelv1.ListStreamsdefaultApplicationProblemPlusJSONResponse{Body: s.opsProblem(err), StatusCode: http.StatusServiceUnavailable}, nil
	}
	return out, nil
}

func (s *server) ResumeStream(ctx context.Context, req cpanelv1.ResumeStreamRequestObject) (cpanelv1.ResumeStreamResponseObject, error) {
	p, kind := s.gate(ctx, true)
	switch kind {
	case gateUnauth:
		return cpanelv1.ResumeStream401ApplicationProblemPlusJSONResponse{UnauthenticatedApplicationProblemPlusJSONResponse: unauthProblem("")}, nil
	case gateCSRF:
		return cpanelv1.ResumeStream403ApplicationProblemPlusJSONResponse(forbidden("origin is not allowed")), nil
	case gateUnavailable:
		return cpanelv1.ResumeStreamdefaultApplicationProblemPlusJSONResponse{
			Body: problemValue(http.StatusServiceUnavailable, "unavailable", "Unavailable", ""), StatusCode: http.StatusServiceUnavailable,
		}, nil
	}
	stream, cleared, err := scanStreamReason(s.pool.QueryRow(ctx, `
		WITH old AS (
			SELECT id, paused_reason FROM ingest.stream WHERE id = $1 AND paused_reason IS NOT NULL
		)
		UPDATE ingest.stream AS s
		SET paused_reason = NULL, paused_at = NULL, next_due_at = now()
		FROM old
		WHERE s.id = old.id
		RETURNING s.id, s.kind, s.lease_key, s.key,
			CASE WHEN s.paused_reason IS NOT NULL THEN 'paused'
			     WHEN s.consecutive_failures > 0 THEN 'backing_off'
			     ELSE 'ok' END,
			s.paused_reason, s.paused_at, s.next_due_at, s.last_ok_at, s.consecutive_failures,
			s.last_error_class::text, s.last_error, s.created_at, old.paused_reason`, req.Id))
	if errors.Is(err, pgx.ErrNoRows) {
		stream, err = scanStream(s.pool.QueryRow(ctx, streamSelect+` WHERE id = $1`, req.Id))
		if errors.Is(err, pgx.ErrNoRows) {
			return cpanelv1.ResumeStream404ApplicationProblemPlusJSONResponse(problemValue(http.StatusNotFound, "not_found", "Not Found", "")), nil
		}
		if err != nil {
			return cpanelv1.ResumeStreamdefaultApplicationProblemPlusJSONResponse{Body: s.opsProblem(err), StatusCode: http.StatusServiceUnavailable}, nil
		}
		return cpanelv1.ResumeStream200JSONResponse(stream), nil
	}
	if err != nil {
		return cpanelv1.ResumeStreamdefaultApplicationProblemPlusJSONResponse{Body: s.opsProblem(err), StatusCode: http.StatusServiceUnavailable}, nil
	}
	s.audit(ctx, p.auditID(), "stream.resume", fmt.Sprintf("stream:%d", stream.Id),
		map[string]any{"paused_reason": cleared}, map[string]any{"status": http.StatusOK})
	return cpanelv1.ResumeStream200JSONResponse(stream), nil
}

func (s *server) ListJobs(ctx context.Context, req cpanelv1.ListJobsRequestObject) (cpanelv1.ListJobsResponseObject, error) {
	if resp, ok := jobGate(s.gate(ctx, false)); !ok {
		return resp, nil
	}
	rows, err := s.pool.Query(ctx, `
		SELECT j.id, j.kind, j.dedup_key, j.lease_key, j.status, j.attempt, j.not_before, j.enqueued_at,
		       j.started_at, j.stream_id, s.last_error_class::text
		FROM ingest.job j
		LEFT JOIN ingest.stream s ON s.id = j.stream_id
		WHERE ($1::text IS NULL OR j.kind = $1)
		  AND ($2::text IS NULL OR j.lease_key = $2)
		  AND ($3::text IS NULL OR j.status = $3)
		  AND ($4::text IS NULL OR s.last_error_class::text = $4)
		ORDER BY j.enqueued_at, j.id
		LIMIT $5`,
		textArg(req.Params.Kind), textArg(req.Params.LeaseKey), textArg(req.Params.Status), textArg(req.Params.ErrorClass),
		bounded(req.Params.Limit, 200, 500))
	if err != nil {
		return cpanelv1.ListJobsdefaultApplicationProblemPlusJSONResponse{Body: s.opsProblem(err), StatusCode: http.StatusServiceUnavailable}, nil
	}
	defer rows.Close()
	out := make(cpanelv1.ListJobs200JSONResponse, 0)
	for rows.Next() {
		var job cpanelv1.Job
		var status string
		if err := rows.Scan(&job.Id, &job.Kind, &job.DedupKey, &job.LeaseKey, &status, &job.Attempt, &job.NotBefore, &job.EnqueuedAt,
			&job.StartedAt, &job.StreamId, &job.LastErrorClass); err != nil {
			return cpanelv1.ListJobsdefaultApplicationProblemPlusJSONResponse{Body: s.opsProblem(err), StatusCode: http.StatusServiceUnavailable}, nil
		}
		job.Status = cpanelv1.JobStatus(status)
		out = append(out, job)
	}
	if err := rows.Err(); err != nil {
		return cpanelv1.ListJobsdefaultApplicationProblemPlusJSONResponse{Body: s.opsProblem(err), StatusCode: http.StatusServiceUnavailable}, nil
	}
	return out, nil
}

func (s *server) ListJobRuns(ctx context.Context, req cpanelv1.ListJobRunsRequestObject) (cpanelv1.ListJobRunsResponseObject, error) {
	if resp, ok := runGate(s.gate(ctx, false)); !ok {
		return resp, nil
	}
	var cursorAt any
	cursorID := int64(0)
	if req.Params.Cursor != nil && strings.TrimSpace(*req.Params.Cursor) != "" {
		at, id, err := decodeCursor(strings.TrimSpace(*req.Params.Cursor))
		if err != nil {
			return cpanelv1.ListJobRuns400ApplicationProblemPlusJSONResponse(problemValue(http.StatusBadRequest, "bad_request", "Bad Request", "cursor")), nil
		}
		cursorAt = at
		cursorID = id
	}
	limit := bounded(req.Params.Limit, 50, 200)
	rows, err := s.pool.Query(ctx, `
		SELECT id, kind, dedup_key, lease_key, stream_id, status, attempt, requests, items, more,
		       error_class::text, error, enqueued_at, started_at, finished_at, worker
		FROM ingest.job_run
		WHERE ($1::timestamptz IS NULL OR finished_at >= $1)
		  AND ($2::timestamptz IS NULL OR finished_at < $2)
		  AND ($3::text IS NULL OR kind = $3)
		  AND ($4::text IS NULL OR lease_key = $4)
		  AND ($5::text IS NULL OR status = $5)
		  AND ($6::text IS NULL OR error_class::text = $6)
		  AND ($7::timestamptz IS NULL OR (finished_at, id) < ($7::timestamptz, $8::bigint))
		ORDER BY finished_at DESC, id DESC
		LIMIT $9`,
		timeArg(req.Params.From), timeArg(req.Params.To), textArg(req.Params.Kind), textArg(req.Params.LeaseKey),
		textArg(req.Params.Status), textArg(req.Params.ErrorClass), cursorAt, cursorID, limit+1)
	if err != nil {
		return cpanelv1.ListJobRunsdefaultApplicationProblemPlusJSONResponse{Body: s.opsProblem(err), StatusCode: http.StatusServiceUnavailable}, nil
	}
	defer rows.Close()
	runs := make([]cpanelv1.JobRun, 0)
	for rows.Next() {
		var run cpanelv1.JobRun
		var status string
		if err := rows.Scan(&run.Id, &run.Kind, &run.DedupKey, &run.LeaseKey, &run.StreamId, &status, &run.Attempt, &run.Requests, &run.Items, &run.More,
			&run.ErrorClass, &run.Error, &run.EnqueuedAt, &run.StartedAt, &run.FinishedAt, &run.Worker); err != nil {
			return cpanelv1.ListJobRunsdefaultApplicationProblemPlusJSONResponse{Body: s.opsProblem(err), StatusCode: http.StatusServiceUnavailable}, nil
		}
		run.Status = cpanelv1.JobRunStatus(status)
		runs = append(runs, run)
	}
	if err := rows.Err(); err != nil {
		return cpanelv1.ListJobRunsdefaultApplicationProblemPlusJSONResponse{Body: s.opsProblem(err), StatusCode: http.StatusServiceUnavailable}, nil
	}
	page := cpanelv1.JobRunPage{Runs: runs}
	if len(runs) > limit {
		last := runs[limit-1]
		cur := encodeCursor(last.FinishedAt, last.Id)
		page.Runs = runs[:limit]
		page.NextCursor = &cur
	}
	return cpanelv1.ListJobRuns200JSONResponse(page), nil
}

func mirrorGate(_ principal, kind int) (cpanelv1.GetMirrorStatusResponseObject, bool) {
	switch kind {
	case gateOK:
		return nil, true
	case gateUnauth:
		return cpanelv1.GetMirrorStatus401ApplicationProblemPlusJSONResponse{UnauthenticatedApplicationProblemPlusJSONResponse: unauthProblem("")}, false
	default:
		return cpanelv1.GetMirrorStatusdefaultApplicationProblemPlusJSONResponse{
			Body: problemValue(http.StatusServiceUnavailable, "unavailable", "Unavailable", ""), StatusCode: http.StatusServiceUnavailable,
		}, false
	}
}

func streamGate(_ principal, kind int) (cpanelv1.ListStreamsResponseObject, bool) {
	switch kind {
	case gateOK:
		return nil, true
	case gateUnauth:
		return cpanelv1.ListStreams401ApplicationProblemPlusJSONResponse{UnauthenticatedApplicationProblemPlusJSONResponse: unauthProblem("")}, false
	default:
		return cpanelv1.ListStreamsdefaultApplicationProblemPlusJSONResponse{
			Body: problemValue(http.StatusServiceUnavailable, "unavailable", "Unavailable", ""), StatusCode: http.StatusServiceUnavailable,
		}, false
	}
}

func jobGate(_ principal, kind int) (cpanelv1.ListJobsResponseObject, bool) {
	switch kind {
	case gateOK:
		return nil, true
	case gateUnauth:
		return cpanelv1.ListJobs401ApplicationProblemPlusJSONResponse{UnauthenticatedApplicationProblemPlusJSONResponse: unauthProblem("")}, false
	default:
		return cpanelv1.ListJobsdefaultApplicationProblemPlusJSONResponse{
			Body: problemValue(http.StatusServiceUnavailable, "unavailable", "Unavailable", ""), StatusCode: http.StatusServiceUnavailable,
		}, false
	}
}

func runGate(_ principal, kind int) (cpanelv1.ListJobRunsResponseObject, bool) {
	switch kind {
	case gateOK:
		return nil, true
	case gateUnauth:
		return cpanelv1.ListJobRuns401ApplicationProblemPlusJSONResponse{UnauthenticatedApplicationProblemPlusJSONResponse: unauthProblem("")}, false
	default:
		return cpanelv1.ListJobRunsdefaultApplicationProblemPlusJSONResponse{
			Body: problemValue(http.StatusServiceUnavailable, "unavailable", "Unavailable", ""), StatusCode: http.StatusServiceUnavailable,
		}, false
	}
}

func (s *server) opsProblem(err error) cpanelv1.Problem {
	s.log.Error().Err(err).Msg("ops")
	return problemValue(http.StatusServiceUnavailable, "unavailable", "Unavailable", "")
}

type rowScanner interface {
	Scan(dest ...any) error
}

func scanStream(row rowScanner) (cpanelv1.Stream, error) {
	var stream cpanelv1.Stream
	var status string
	err := row.Scan(&stream.Id, &stream.Kind, &stream.LeaseKey, &stream.Key, &status,
		&stream.PausedReason, &stream.PausedAt, &stream.NextDueAt, &stream.LastOkAt, &stream.ConsecutiveFailures,
		&stream.LastErrorClass, &stream.LastError, &stream.CreatedAt)
	stream.Status = cpanelv1.StreamStatus(status)
	return stream, err
}

func scanStreamReason(row rowScanner) (cpanelv1.Stream, string, error) {
	var stream cpanelv1.Stream
	var status, reason string
	err := row.Scan(&stream.Id, &stream.Kind, &stream.LeaseKey, &stream.Key, &status,
		&stream.PausedReason, &stream.PausedAt, &stream.NextDueAt, &stream.LastOkAt, &stream.ConsecutiveFailures,
		&stream.LastErrorClass, &stream.LastError, &stream.CreatedAt, &reason)
	stream.Status = cpanelv1.StreamStatus(status)
	return stream, reason, err
}

func textArg[T ~string](p *T) any {
	if p == nil {
		return nil
	}
	s := strings.TrimSpace(string(*p))
	if s == "" {
		return nil
	}
	return s
}

func timeArg(p *time.Time) any {
	if p == nil {
		return nil
	}
	return *p
}

func bounded(n *int, def, max int) int {
	if n == nil || *n < 1 {
		return def
	}
	if *n > max {
		return max
	}
	return *n
}

func encodeCursor(t time.Time, id int64) string {
	raw := t.UTC().Format(time.RFC3339Nano) + "|" + strconv.FormatInt(id, 10)
	return base64.RawURLEncoding.EncodeToString([]byte(raw))
}

func decodeCursor(s string) (time.Time, int64, error) {
	raw, err := base64.RawURLEncoding.DecodeString(s)
	if err != nil {
		return time.Time{}, 0, err
	}
	text := string(raw)
	i := strings.LastIndex(text, "|")
	if i <= 0 || i == len(text)-1 {
		return time.Time{}, 0, errors.New("cursor")
	}
	t, err := time.Parse(time.RFC3339Nano, text[:i])
	if err != nil {
		return time.Time{}, 0, err
	}
	id, err := strconv.ParseInt(text[i+1:], 10, 64)
	if err != nil {
		return time.Time{}, 0, err
	}
	return t, id, nil
}
