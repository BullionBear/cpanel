package worker

import (
	"context"
	"errors"
	"math/rand/v2"
	"time"

	"github.com/jackc/pgx/v5"

	"github.com/BullionBear/cpanel/internal/classify"
)

const (
	backoffStart = 30 * time.Second
	backoffCap   = 30 * time.Minute
	defaultIPBan = 2 * time.Minute
	maxDelay     = 24 * time.Hour // a bad Retry-After cannot stall a lease for longer than this

	// maintPartitionsKind never calls out, so an egress pause does not hold it. It is the name
	// reconcile.MaintPartitions registers.
	maintPartitionsKind = "maint.partitions"
)

// AuthFailure is an auth failure, as CredentialHook sees it. C5-04 turns it into a credential state.
type AuthFailure struct {
	JobID    int64
	StreamID int64
	LeaseKey string
	Kind     string
	Message  string
}

// Alert is a bad_request failure, as AlertHook sees it. C3-05 delivers it. The stream is already paused.
type Alert struct {
	StreamID int64
	LeaseKey string
	Kind     string
	Class    string
	Message  string
}

// EgressScope is the ingest.pause scope shared by every worker behind one egress.
func EgressScope(name string) string { return "egress:" + name }

// effect is what recording a finished job also does.
type effect struct {
	status      string // succeeded, failed or cancelled
	class, msg  *string
	due         string // "period", "now" or "delay"
	delay       time.Duration
	backoff     bool // delay is the stream's exponential backoff, computed once the failure count is known
	pauseStream bool
	pushLease   bool
	ban         time.Duration // write ingest.pause for this long; zero writes nothing
	banSkipped  bool          // an IP ban with no egress name: recorded, not paused
	auth, alert bool
}

func (r *runner) effectOf(err error) effect {
	text := err.Error()
	if len(text) > maxErrorLen {
		text = text[:maxErrorLen]
	}
	msg := &text
	if errors.Is(err, ErrCursorMoved) {
		return effect{status: "cancelled", msg: msg, due: "now"}
	}
	eff := effect{status: "failed", class: strptr("internal"), msg: msg, due: "period"}
	var ce *classify.ClassifiedError
	if !errors.As(err, &ce) || !ce.Class.Known() {
		return eff
	}
	eff.class = strptr(string(ce.Class))
	switch ce.Class {
	case classify.Auth:
		eff.auth = true
	case classify.RateLimited:
		eff.pushLease = true
		if ce.RetryAfter > 0 {
			eff.due, eff.delay = "delay", capDelay(ce.RetryAfter)
		} else {
			eff.backoff = true
		}
	case classify.IPBanned:
		eff.due = "now"
		if r.egress == "" {
			eff.banSkipped = true
			break
		}
		eff.ban = defaultIPBan
		if ce.RetryAfter > 0 {
			eff.ban = capDelay(ce.RetryAfter)
		}
	case classify.Unavailable:
		eff.backoff = true
	case classify.BadRequest:
		eff.due, eff.pauseStream, eff.alert = "now", true, true
	case classify.NotFound:
		// The handler decided to return it. No pause, no backoff, no hook.
	default:
		eff.class = strptr("internal")
	}
	return eff
}

func strptr(s string) *string { return &s }

func capDelay(d time.Duration) time.Duration {
	if d < 0 {
		return 0
	}
	if d > maxDelay {
		return maxDelay
	}
	return d
}

// backoff is the unavailable and header-less rate-limit delay for the failure-th consecutive failure, counting
// from 1: 30s, doubled each time, capped at 30 minutes, then multiplied by a factor in [0.5, 1]. unit is that
// factor's position, clamped to [0, 1].
func backoff(failures int, unit float64) time.Duration {
	if failures < 1 {
		failures = 1
	}
	d := backoffStart
	for range failures - 1 {
		if d >= backoffCap/2 {
			d = backoffCap
			break
		}
		d *= 2
	}
	switch {
	case unit <= 0:
		return d / 2
	case unit >= 1:
		return d
	default:
		return time.Duration(float64(d) * (0.5 + 0.5*unit))
	}
}

// unit is the jitter position in [0, 1].
func (r *runner) unit() float64 {
	if r.rand == nil {
		return rand.Float64()
	}
	return r.rand()
}

// scope is this worker's pause scope. An empty egress name still has one, and nothing is written under it.
func (r *runner) scope() string { return EgressScope(r.egress) }

// recordFailure writes eff on top of the job_run row the caller inserted, and returns the alert to send after the
// transaction commits. It does nothing visible when the job was no longer ours.
func (r *runner) recordFailure(ctx context.Context, tx pgx.Tx, j *Job, eff *effect) (*Alert, error) {
	if eff.backoff {
		n := 0
		if j.StreamID != 0 {
			if err := tx.QueryRow(ctx, `SELECT consecutive_failures FROM ingest.stream WHERE id = $1`, j.StreamID).Scan(&n); err != nil {
				return nil, err
			}
		}
		eff.delay = backoff(n+1, r.unit())
		eff.due = "delay"
	}
	if j.StreamID != 0 {
		if _, err := tx.Exec(ctx, `
			UPDATE ingest.stream SET
				next_due_at          = CASE $2
				                         WHEN 'now' THEN now()
				                         WHEN 'delay' THEN now() + $3::interval
				                         ELSE now() + period END,
				last_ok_at           = CASE WHEN $4 = 'succeeded' THEN now() ELSE last_ok_at END,
				consecutive_failures = CASE $4 WHEN 'succeeded' THEN 0
				                               WHEN 'failed' THEN consecutive_failures + 1
				                               ELSE consecutive_failures END,
				last_error_at        = CASE WHEN $4 = 'failed' THEN now() ELSE last_error_at END,
				last_error_class     = CASE WHEN $4 = 'failed' THEN $5::text ELSE last_error_class END,
				last_error           = CASE WHEN $4 = 'failed' THEN $6::text ELSE last_error END,
				paused_reason        = CASE WHEN $7 THEN 'bad_request' ELSE paused_reason END,
				paused_at            = CASE WHEN $7 THEN now() ELSE paused_at END
			WHERE id = $1`,
			j.StreamID, eff.due, eff.delay, eff.status, eff.class, eff.msg, eff.pauseStream); err != nil {
			return nil, err
		}
	}
	if eff.auth && r.auth != nil {
		if err := r.auth(ctx, tx, AuthFailure{
			JobID: j.ID, StreamID: j.StreamID, LeaseKey: j.LeaseKey, Kind: j.Kind, Message: deref(eff.msg),
		}); err != nil {
			return nil, err
		}
	}
	if eff.pushLease {
		if _, err := tx.Exec(ctx, `
			UPDATE ingest.job SET not_before = greatest(not_before, now() + $2::interval)
			WHERE lease_key = $1 AND status = 'pending'`,
			j.LeaseKey, eff.delay); err != nil {
			return nil, err
		}
		if _, err := tx.Exec(ctx, `
			UPDATE ingest.stream SET next_due_at = greatest(next_due_at, now() + $2::interval)
			WHERE lease_key = $1 AND id <> $3`,
			j.LeaseKey, eff.delay, j.StreamID); err != nil {
			return nil, err
		}
	}
	if eff.ban > 0 {
		reason := "ip_banned"
		if eff.msg != nil && *eff.msg != "" {
			reason = *eff.msg
			if len(reason) > 500 {
				reason = reason[:500]
			}
		}
		if _, err := tx.Exec(ctx, `
			INSERT INTO ingest.pause (scope, until, reason)
			VALUES ($1, now() + $2::interval, $3)
			ON CONFLICT (scope) DO UPDATE
			SET until = greatest(ingest.pause.until, excluded.until), reason = excluded.reason, set_at = now()`,
			r.scope(), eff.ban, reason); err != nil {
			return nil, err
		}
	}
	if !eff.alert {
		return nil, nil
	}
	return &Alert{
		StreamID: j.StreamID, LeaseKey: j.LeaseKey, Kind: j.Kind, Class: "bad_request", Message: deref(eff.msg),
	}, nil
}

func deref(s *string) string {
	if s == nil {
		return ""
	}
	return *s
}
