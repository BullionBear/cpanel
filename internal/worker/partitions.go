package worker

import (
	"context"

	"github.com/jackc/pgx/v5"
)

const (
	partitionDaysAhead = 7  // UTC days after today whose job_run partition is made ahead
	jobRunKeepDays     = 30 // job_run partitions older than this are dropped
	rawMonthsAhead     = 1  // UTC months after this one whose raw partition is made ahead
)

// MaintPartitions runs maint.partitions (C3-01's functions): it makes the job_run partitions for today and the next
// 7 UTC days, drops the partitions of days more than 30 days back after copying their failed runs into
// ingest.job_failure, and deletes job_failure rows older than a year. It also makes this month's raw partitions
// and the next month's for venue_snapshot, venue_trade, venue_order and venue_income (C6-02, C7-01). Those are
// kept forever. Items is the partitions made and dropped plus the failures deleted.
//
// Each step is its own transaction. A reader of job_run locks the parent and then each partition; one transaction
// that attaches a partition (locking the default partition) and then drops one (locking the parent) deadlocks with
// it. The lock timeout keeps a step queued behind a long reader from stalling every job completion behind it.
func MaintPartitions(ctx context.Context, job *Job) (Result, error) {
	var made, dropped, deleted int64
	step := func(sql string, args ...any) (int64, error) {
		var n int64
		err := job.CommitPage(ctx, func(tx pgx.Tx) error {
			if _, err := tx.Exec(ctx, `SET LOCAL lock_timeout = '5s'`); err != nil {
				return err
			}
			return tx.QueryRow(ctx, sql, args...).Scan(&n)
		}, nil)
		return n, err
	}
	for d := range partitionDaysAhead + 1 {
		n, err := step(`SELECT ingest.ensure_job_run_partition((now() AT TIME ZONE 'UTC')::date + $1::int)::int`, d)
		if err != nil {
			return Result{Items: int(made)}, err
		}
		made += n
	}
	var months int64
	for _, parent := range []string{"venue_snapshot", "venue_trade", "venue_order", "venue_income"} {
		for m := range rawMonthsAhead + 1 {
			n, err := step(`
				SELECT raw.ensure_month_partition($1, (date_trunc('month', now() AT TIME ZONE 'UTC') + $2::int * interval '1 month')::date)::int`,
				parent, m)
			if err != nil {
				return Result{Items: int(made + months)}, err
			}
			months += n
		}
	}
	dropped, err := step(`SELECT ingest.expire_job_runs((now() AT TIME ZONE 'UTC')::date - $1::int)`, jobRunKeepDays)
	if err != nil {
		return Result{Items: int(made + months)}, err
	}
	deleted, err = step(`
		WITH gone AS (DELETE FROM ingest.job_failure WHERE finished_at < now() - interval '1 year' RETURNING 1)
		SELECT count(*) FROM gone`)
	if err != nil {
		return Result{Items: int(made + months + dropped)}, err
	}
	job.r.log.Info().Int64("made", made).Int64("months", months).Int64("dropped", dropped).Int64("failures_deleted", deleted).Msg("job_run partitions")
	return Result{Items: int(made + months + dropped + deleted)}, nil
}
