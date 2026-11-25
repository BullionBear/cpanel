package reconcile

import (
	"context"
	"fmt"
	"slices"
	"time"

	"github.com/jackc/pgx/v5"
)

// SnapshotKind is the hourly snapshot job. The worker that has a handler for a
// kind passes that kind to Snapshots; this package does not name venues.
const SnapshotKind = "snapshot"

// SnapshotRetryAfter is how long a failed snapshot run of a key blocks the next insert
// of that key (C6-D006). A failure early in the hour is tried again within StaleAfter.
const SnapshotRetryAfter = 2 * time.Minute

// SnapshotDedup is the unfinished-job key for one account and one UTC hour (C3-D002).
func SnapshotDedup(accountID int64, bucket time.Time) string {
	return fmt.Sprintf("bucket:snapshot:account:%d:%s", accountID, bucket.UTC().Format(time.RFC3339))
}

// Snapshots inserts one snapshot job per account for the UTC hour of now.
// Past hours are not backfilled. An account qualifies when it is not deleted,
// has an active read credential, and has an enabled kind in kinds. kinds is the
// list the worker can snapshot; an empty list inserts nothing.
//
// A batch already stored for that hour, an unfinished job, a succeeded run of
// the same key (including a run that refused a late capture), or a failed run of
// the same key that finished less than SnapshotRetryAfter ago blocks another insert.
func Snapshots(kinds []string) Rule {
	if kinds == nil {
		kinds = []string{}
	} else {
		kinds = slices.Clone(kinds)
	}
	return Rule{Name: "snapshots", Run: func(ctx context.Context, tx pgx.Tx, now time.Time) (int, error) {
		tag, err := tx.Exec(ctx, `
			WITH due AS (
				SELECT a.id AS account_id,
				       b.bucket,
				       'bucket:snapshot:account:' || a.id || ':' ||
				           to_char(b.bucket AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS dedup_key
				FROM pms.exchange_account a
				CROSS JOIN LATERAL (
					SELECT (date_trunc('hour', $2::timestamptz AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') AS bucket
				) b
				WHERE a.deleted_at IS NULL
				  AND EXISTS (
				      SELECT 1 FROM pms.credential c
				      WHERE c.exchange_account_id = a.id
				        AND c.purpose = 'read'
				        AND c.status = 'active'
				        AND octet_length(c.ciphertext) > 0
				  )
				  AND EXISTS (
				      SELECT 1 FROM pms.exchange_account_kind k
				      WHERE k.exchange_account_id = a.id
				        AND k.disabled_at IS NULL
				        AND k.kind = ANY($1::text[])
				  )
				ORDER BY a.id
			)
			INSERT INTO ingest.job (kind, dedup_key, lease_key, params)
			SELECT $3, due.dedup_key, 'account:' || due.account_id,
			       jsonb_build_object(
			           'exchange_account_id', due.account_id,
			           'bucket', to_char(due.bucket AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
			       )
			FROM due
			WHERE NOT EXISTS (
			    SELECT 1 FROM ledger.snapshot_batch s
			    WHERE s.exchange_account_id = due.account_id AND s.bucket = due.bucket
			)
			  AND NOT EXISTS (
			    SELECT 1 FROM ingest.job j WHERE j.dedup_key = due.dedup_key
			  )
			  AND NOT EXISTS (
			    SELECT 1 FROM ingest.job_run r
			    WHERE r.dedup_key = due.dedup_key AND r.status = 'succeeded'
			  )
			  AND NOT EXISTS (
			    SELECT 1 FROM ingest.job_run r
			    WHERE r.dedup_key = due.dedup_key AND r.status = 'failed'
			      AND r.finished_at > $2::timestamptz - $4::interval
			  )
			ORDER BY due.account_id
			ON CONFLICT (dedup_key) DO NOTHING`,
			kinds, now, SnapshotKind, SnapshotRetryAfter)
		if err != nil {
			return 0, err
		}
		return int(tag.RowsAffected()), nil
	}}
}
