package worker

import (
	"context"
	"errors"
	"sync/atomic"
	"time"

	"github.com/jackc/pgx/v5"
)

// lease is one held ingest.lease row. Lease times are the database's now(); until is this process's own,
// conservative view of when the lease ends.
type lease struct {
	key   string
	token int64
	gone  atomic.Bool
	until atomic.Int64 // UnixNano, local clock: the start of the last successful renewal plus the TTL
}

// lost reports whether the lease is gone or ends within margin. No page starts then.
func (l *lease) lost(margin time.Duration) bool {
	return l.gone.Load() || time.Now().Add(margin).UnixNano() > l.until.Load()
}

// acquire takes the lease of the most urgent lease_key that has no live holder and has a job of a kind this worker
// runs that is due or that an earlier holder left running; it returns nil when there is none. In the same
// transaction it sets those running jobs back to pending, counting an attempt: their holder died or lost the lease.
func (r *runner) acquire(ctx context.Context) (*lease, error) {
	start := time.Now()
	l := &lease{}
	var took int64
	err := pgx.BeginFunc(ctx, r.db, func(tx pgx.Tx) error {
		err := tx.QueryRow(ctx, `
			WITH candidate AS (
				SELECT j.lease_key
				FROM ingest.job j
				LEFT JOIN ingest.lease l ON l.key = j.lease_key
				WHERE (j.status = 'running' OR j.not_before <= now())
				  AND j.kind = ANY($3)
				  AND (j.kind = ANY($4) OR NOT EXISTS (
				        SELECT FROM ingest.pause p WHERE p.scope = $5 AND p.until > now()))
				  AND (l.key IS NULL OR l.until <= now())
				GROUP BY j.lease_key
				ORDER BY min(j.priority), min(j.not_before), j.lease_key
				LIMIT 1
			)
			INSERT INTO ingest.lease AS l (key, owner, token, acquired_at, until)
			SELECT lease_key, $1, 1, now(), now() + $2::interval FROM candidate
			ON CONFLICT (key) DO UPDATE
			SET owner = excluded.owner, token = l.token + 1, acquired_at = excluded.acquired_at, until = excluded.until
			WHERE l.until <= now()
			RETURNING l.key, l.token`,
			r.name, r.ttl, r.kinds, r.local, r.scope()).Scan(&l.key, &l.token)
		if err != nil {
			return err
		}
		tag, err := tx.Exec(ctx, `
			UPDATE ingest.job
			SET status = 'pending', started_at = NULL, worker = NULL, lease_token = NULL, attempt = attempt + 1
			WHERE lease_key = $1 AND status = 'running' AND lease_token <> $2`,
			l.key, l.token)
		took = tag.RowsAffected()
		return err
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	l.until.Store(start.Add(r.ttl).UnixNano())
	if took > 0 {
		r.log.Warn().Str("lease_key", l.key).Int64("token", l.token).Int64("jobs", took).
			Msg("took over jobs left running by an earlier holder")
	} else {
		r.log.Debug().Str("lease_key", l.key).Int64("token", l.token).Msg("lease acquired")
	}
	return l, nil
}

// keep renews l every RenewEvery until ctx ends. When the row says the lease is no longer ours, l is lost. A
// failed renewal is retried on the next tick; l.lost turns true by itself if none succeeds in time.
func (r *runner) keep(ctx context.Context, l *lease) {
	t := time.NewTicker(r.renew)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
		start := time.Now()
		ok, err := r.renewLease(ctx, l)
		switch {
		case ctx.Err() != nil:
			return
		case err != nil:
			r.log.Warn().Err(err).Str("lease_key", l.key).Msg("renew lease")
		case !ok:
			l.gone.Store(true)
			r.log.Warn().Str("lease_key", l.key).Int64("token", l.token).Msg("lease lost")
			return
		default:
			l.until.Store(start.Add(r.ttl).UnixNano())
		}
	}
}

// renewLease extends l by the TTL if it is still ours and has not run out.
func (r *runner) renewLease(ctx context.Context, l *lease) (bool, error) {
	tag, err := r.db.Exec(ctx, `
		UPDATE ingest.lease SET until = now() + $4::interval
		WHERE key = $1 AND owner = $2 AND token = $3 AND until > now()`,
		l.key, r.name, l.token, r.ttl)
	return tag.RowsAffected() == 1, err
}

// release ends l now, so another worker can take the key without waiting for the TTL.
func (r *runner) release(ctx context.Context, l *lease) error {
	_, err := r.db.Exec(ctx, `
		UPDATE ingest.lease SET until = now()
		WHERE key = $1 AND owner = $2 AND token = $3 AND until > now()`,
		l.key, r.name, l.token)
	return err
}
