package reconcile

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5"
)

// Rules are the rules in the order a round runs them. Registration comes before dispatch, so a singleton gets its
// first job in the round that registers it. Later tickets add theirs.
//
// Replicas run the same statements at the same time. Each rule takes its keys in one fixed order, so a replica
// that hits another's uncommitted key waits for it instead of deadlocking.
func Rules() []Rule {
	return []Rule{MaintPartitions, MftikoSync, SymSync, Streams, Periodics}
}

// MaintPartitions registers maint.partitions, which makes and expires the ingest.job_run partitions every hour
// (C3-03 runs it).
var MaintPartitions = Singleton("maint.partitions", "global:maint", time.Hour)

// MftikoSync registers mftiko.sync, which copies mftiko's accounts into mirror.account every minute (C4-01).
var MftikoSync = Singleton("mftiko.sync", "global:mftiko", time.Minute)

// SymSync registers sym.sync, which copies SYM's instruments into mirror.symbol every minute (C4-02).
var SymSync = Singleton("sym.sync", "global:sym", time.Minute)

// Singleton returns a rule that inserts the ingest.periodic row name, whose jobs have kind name, due at once. It
// inserts the row only when it is missing: after that the row is the operator's, and a changed period or
// enabled = false stays.
func Singleton(name, leaseKey string, period time.Duration) Rule {
	return Rule{Name: name, Run: func(ctx context.Context, tx pgx.Tx, now time.Time) (int, error) {
		tag, err := tx.Exec(ctx, `
			INSERT INTO ingest.periodic (name, kind, lease_key, period, next_due_at)
			VALUES ($1, $1, $2, $3, $4)
			ON CONFLICT (name) DO NOTHING`,
			name, leaseKey, period, now)
		return int(tag.RowsAffected()), err
	}}
}

// Streams inserts one job for every stream that is due, not paused, and has no unfinished job. The worker moves
// the stream's next_due_at when the job ends (C3-03). NOT EXISTS skips streams whose job is already there without
// spending a job id; ON CONFLICT covers a replica inserting the same job at the same time.
var Streams = Rule{Name: "streams", Run: func(ctx context.Context, tx pgx.Tx, now time.Time) (int, error) {
	tag, err := tx.Exec(ctx, `
		INSERT INTO ingest.job (kind, dedup_key, lease_key, stream_id)
		SELECT s.kind, 'stream:' || s.id, s.lease_key, s.id
		FROM ingest.stream s
		WHERE s.next_due_at <= $1::timestamptz
		  AND s.paused_reason IS NULL
		  AND NOT EXISTS (SELECT FROM ingest.job j WHERE j.dedup_key = 'stream:' || s.id)
		ORDER BY s.id
		ON CONFLICT (dedup_key) DO NOTHING`,
		now)
	return int(tag.RowsAffected()), err
}}

// Periodics inserts one job for every enabled periodic that is due and has no unfinished job, and moves its
// next_due_at to now + period: the interval counts from dispatch, and a job still unfinished when the next is due
// holds it back until it ends. A replica skips the rows another one has locked, since that one is dispatching
// them.
var Periodics = Rule{Name: "periodics", Run: func(ctx context.Context, tx pgx.Tx, now time.Time) (int, error) {
	tag, err := tx.Exec(ctx, `
		WITH due AS (
			SELECT p.name
			FROM ingest.periodic p
			WHERE p.enabled
			  AND p.next_due_at <= $1::timestamptz
			  AND NOT EXISTS (SELECT FROM ingest.job j WHERE j.dedup_key = 'periodic:' || p.name)
			ORDER BY p.name
			FOR NO KEY UPDATE SKIP LOCKED
		), moved AS (
			UPDATE ingest.periodic p
			SET next_due_at = $1::timestamptz + p.period
			FROM due
			WHERE p.name = due.name
			RETURNING p.name, p.kind, p.lease_key, p.params
		)
		INSERT INTO ingest.job (kind, dedup_key, lease_key, periodic_name, params)
		SELECT kind, 'periodic:' || name, lease_key, name, params
		FROM moved
		ORDER BY name
		ON CONFLICT (dedup_key) DO NOTHING`,
		now)
	return int(tag.RowsAffected()), err
}}
