package mirror

import (
	"context"

	"github.com/jackc/pgx/v5"

	"github.com/BullionBear/cpanel/internal/classify"
	"github.com/BullionBear/cpanel/internal/worker"
)

// fillScopeUIDs runs after the table reload. A scope UID that mirror.symbol does not have is
// fetched with GetByUid(include_inactive=true). NotFound and a ticker that does not parse are
// recorded and do not fail the job. Any other error does (C4-D008, C4-D012).
func fillScopeUIDs(ctx context.Context, job *worker.Job, client Catalog, res *worker.Result) error {
	var uids []int64
	err := job.CommitPage(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT s.uid
			FROM (SELECT DISTINCT uid FROM mirror.scope) s
			LEFT JOIN mirror.symbol y ON y.uid = s.uid
			LEFT JOIN mirror.uid_lookup u ON u.uid = s.uid
			WHERE y.uid IS NULL
			ORDER BY u.last_tried_at NULLS FIRST, s.uid
			LIMIT $1`, maxScopeUIDs)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var uid int64
			if err := rows.Scan(&uid); err != nil {
				return err
			}
			uids = append(uids, uid)
		}
		return rows.Err()
	}, nil)
	if err != nil {
		return err
	}
	for _, uid := range uids {
		if err := symBudget(ctx, job); err != nil {
			return err
		}
		inst, err := client.GetByUid(ctx, uint32(uid), true)
		res.Requests++
		if classify.Is(err, classify.NotFound) {
			if err := job.CommitPage(ctx, func(tx pgx.Tx) error {
				return noteUIDLookup(ctx, tx, uid, "not_found")
			}, nil); err != nil {
				return err
			}
			continue
		}
		if err != nil {
			return err
		}
		if inst.Venue == "" || inst.Category == "" {
			if err := job.CommitPage(ctx, func(tx pgx.Tx) error {
				return noteUIDLookup(ctx, tx, uid, "unparsable")
			}, nil); err != nil {
				return err
			}
			continue
		}
		inst.Active = inst.Status != "EXPIRED" && inst.Status != "DELISTED"
		if inst.UID == 0 {
			inst.UID = uint32(uid)
		}
		if err := job.CommitPage(ctx, func(tx pgx.Tx) error {
			if err := upsertSymbol(ctx, tx, inst); err != nil {
				return err
			}
			_, err := tx.Exec(ctx, `DELETE FROM mirror.uid_lookup WHERE uid = $1`, int64(inst.UID))
			return err
		}, nil); err != nil {
			return err
		}
		res.Items++
	}
	return nil
}

func noteUIDLookup(ctx context.Context, tx pgx.Tx, uid int64, result string) error {
	_, err := tx.Exec(ctx, `
		INSERT INTO mirror.uid_lookup (uid, first_tried_at, last_tried_at, attempts, last_result)
		VALUES ($1, now(), now(), 1, $2)
		ON CONFLICT (uid) DO UPDATE SET
			last_tried_at = now(),
			attempts = mirror.uid_lookup.attempts + 1,
			last_result = EXCLUDED.last_result`, uid, result)
	return err
}
