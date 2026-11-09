package mirror

import (
	"context"
	"sort"
	"time"

	"github.com/jackc/pgx/v5"

	"github.com/BullionBear/cpanel/internal/classify"
	"github.com/BullionBear/cpanel/internal/mftiko"
	"github.com/BullionBear/cpanel/internal/worker"
)

// syncScopes is the second stage of mftiko.sync. It runs only after the account stage returned
// nil, so a failed account read does not touch scopes. Each page is its own transaction. The
// cursor is not kept for the next job. Rows are never deleted. since only moves earlier.
// last_complete_at moves only when every page's unavailable regions are empty (C4-D009).
func syncScopes(ctx context.Context, job *worker.Job, client Lister, res *worker.Result) error {
	if err := job.CommitPage(ctx, func(tx pgx.Tx) error { return touchScope(ctx, tx) }, nil); err != nil {
		return err
	}
	cursor := ""
	var regions []string
	seen := map[string]struct{}{}
	for page := 0; ; page++ {
		if err := ctx.Err(); err != nil {
			return err
		}
		if page >= maxPages {
			return &classify.ClassifiedError{Class: classify.Internal, Detail: "mftiko.sync passed the page limit of 200"}
		}
		if !time.Now().Before(job.Budget.Deadline) {
			return &classify.ClassifiedError{Class: classify.Internal, Detail: "mftiko.sync stopped at the time budget"}
		}
		got, err := client.ListScopes(ctx, cursor)
		if err != nil {
			return err
		}
		res.Requests++
		for _, region := range got.Unavailable {
			if _, ok := seen[region]; ok {
				continue
			}
			seen[region] = struct{}{}
			regions = append(regions, region)
		}
		last := got.Next == ""
		err = job.CommitPage(ctx, func(tx pgx.Tx) error {
			if err := upsertScopes(ctx, tx, got.Scopes); err != nil {
				return err
			}
			if last {
				sort.Strings(regions)
				return markScopes(ctx, tx, len(regions) == 0, regions)
			}
			return nil
		}, nil)
		if err != nil {
			return err
		}
		if last {
			return nil
		}
		cursor = got.Next
	}
}

func touchScope(ctx context.Context, tx pgx.Tx) error {
	_, err := tx.Exec(ctx, `
		INSERT INTO mirror.sync_state (source, last_attempt_at)
		VALUES ($1, now())
		ON CONFLICT (source) DO UPDATE SET last_attempt_at = now()`, SourceScopes)
	return err
}

// markScopes records a finished scope round. items is the number of rows mirror.scope still
// holds, which is what Ops shows: an empty response does not shrink the table. complete is
// false when any page listed an unavailable region, and then last_complete_at stays put.
func markScopes(ctx context.Context, tx pgx.Tx, complete bool, regions []string) error {
	if regions == nil {
		regions = []string{}
	}
	_, err := tx.Exec(ctx, `
		UPDATE mirror.sync_state
		SET last_ok_at = now(),
		    last_complete_at = CASE WHEN $2 THEN now() ELSE last_complete_at END,
		    items = (SELECT count(*) FROM mirror.scope),
		    unavailable_regions = $3
		WHERE source = $1`, SourceScopes, complete, regions)
	return err
}

// upsertScopes copies one page. A later since is ignored. An earlier one replaces the stored
// value and stamps since_lowered_at. Kind is not consulted: BinancePM and BinanceEO scopes are
// copied too (C4-D017).
func upsertScopes(ctx context.Context, tx pgx.Tx, scopes []mftiko.Scope) error {
	for _, scope := range scopes {
		if _, err := tx.Exec(ctx, `
			INSERT INTO mirror.scope (api_id, uid, since, first_seen_at, last_seen_at)
			VALUES ($1, $2, $3, now(), now())
			ON CONFLICT (api_id, uid) DO UPDATE SET
				last_seen_at = now(),
				since_lowered_at = CASE
					WHEN EXCLUDED.since < mirror.scope.since THEN now()
					ELSE mirror.scope.since_lowered_at
				END,
				since = LEAST(mirror.scope.since, EXCLUDED.since)`,
			scope.APIID, scope.UID, scope.Since); err != nil {
			return err
		}
	}
	return nil
}
