// Package mirror writes mirror.* from mftiko and SYM. Only cpanel-worker links it. cpanel-api
// reads the tables with its own SQL (C4-D002).
package mirror

import (
	"context"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"

	"github.com/BullionBear/cpanel/internal/classify"
	"github.com/BullionBear/cpanel/internal/mftiko"
	"github.com/BullionBear/cpanel/internal/worker"
)

const (
	// SourceAccounts is mirror.sync_state.source for the account copy.
	SourceAccounts = "mftiko.accounts"

	// maxPages bounds one mftiko.sync round. The 20-request job budget does not: a partial read
	// is not a complete copy (C4-D009).
	maxPages = 200
)

// Lister is the part of the mftiko client mftiko.sync uses.
type Lister interface {
	ListAccounts(ctx context.Context, cursor string) (mftiko.Page, error)
}

// MftikoSync is the mftiko.sync handler. A nil client fails the job without calling out, so a
// worker that has no token still records the failure instead of leaving the job pending.
func MftikoSync(client Lister) worker.Handler {
	return func(ctx context.Context, job *worker.Job) (worker.Result, error) {
		if client == nil {
			return worker.Result{}, &classify.ClassifiedError{Class: classify.Unavailable, Detail: "mftiko URL or token is not set"}
		}
		if err := job.CommitPage(ctx, func(tx pgx.Tx) error { return touchAttempt(ctx, tx) }, nil); err != nil {
			return worker.Result{}, err
		}
		var res worker.Result
		cursor := ""
		for page := 0; ; page++ {
			if err := ctx.Err(); err != nil {
				return res, err
			}
			if page >= maxPages {
				return res, &classify.ClassifiedError{Class: classify.Internal, Detail: "mftiko.sync passed the page limit of 200"}
			}
			if !time.Now().Before(job.Budget.Deadline) {
				return res, &classify.ClassifiedError{Class: classify.Internal, Detail: "mftiko.sync stopped at the time budget"}
			}
			got, err := client.ListAccounts(ctx, cursor)
			if err != nil {
				return res, err
			}
			res.Requests++
			seen := res.Items + len(got.Accounts)
			last := got.Next == ""
			err = job.CommitPage(ctx, func(tx pgx.Tx) error {
				if err := upsertAccounts(ctx, tx, got.Accounts); err != nil {
					return err
				}
				if last {
					return markComplete(ctx, tx, seen)
				}
				return nil
			}, nil)
			if err != nil {
				return res, err
			}
			res.Items = seen
			if last {
				return res, nil
			}
			cursor = got.Next
		}
	}
}

func touchAttempt(ctx context.Context, tx pgx.Tx) error {
	_, err := tx.Exec(ctx, `
		INSERT INTO mirror.sync_state (source, last_attempt_at)
		VALUES ($1, now())
		ON CONFLICT (source) DO UPDATE SET last_attempt_at = now()`, SourceAccounts)
	return err
}

func markComplete(ctx context.Context, tx pgx.Tx, items int) error {
	_, err := tx.Exec(ctx, `
		UPDATE mirror.sync_state
		SET last_ok_at = now(), last_complete_at = now(), items = $2, unavailable_regions = '{}'
		WHERE source = $1`, SourceAccounts, items)
	return err
}

// upsertAccounts copies one page. A row is never deleted. account_revision does not move
// backwards, and an older revision does not change name, kind or deleted_at. exchange and
// exch_account_uid, once set, stay; a different non-empty pair is recorded and refused.
func upsertAccounts(ctx context.Context, tx pgx.Tx, accounts []mftiko.Account) error {
	for _, account := range accounts {
		if _, err := tx.Exec(ctx, `
			INSERT INTO mirror.account (
				api_id, name, kind, exchange, exch_account_uid, account_revision, deleted_at, first_seen_at, last_seen_at)
			VALUES ($1, $2, $3, $4, $5, $6, $7, now(), now())
			ON CONFLICT (api_id) DO UPDATE SET
				last_seen_at = now(),
				name = CASE WHEN EXCLUDED.account_revision >= mirror.account.account_revision THEN EXCLUDED.name ELSE mirror.account.name END,
				kind = CASE WHEN EXCLUDED.account_revision >= mirror.account.account_revision THEN EXCLUDED.kind ELSE mirror.account.kind END,
				deleted_at = CASE WHEN EXCLUDED.account_revision >= mirror.account.account_revision THEN EXCLUDED.deleted_at ELSE mirror.account.deleted_at END,
				account_revision = GREATEST(mirror.account.account_revision, EXCLUDED.account_revision),
				exchange = CASE WHEN mirror.account.exchange IS NULL THEN EXCLUDED.exchange ELSE mirror.account.exchange END,
				exch_account_uid = CASE WHEN mirror.account.exch_account_uid IS NULL THEN EXCLUDED.exch_account_uid ELSE mirror.account.exch_account_uid END,
				identity_conflict = CASE
					WHEN (mirror.account.exchange IS NOT NULL AND EXCLUDED.exchange IS NOT NULL
					      AND mirror.account.exchange IS DISTINCT FROM EXCLUDED.exchange)
					  OR (mirror.account.exch_account_uid IS NOT NULL AND EXCLUDED.exch_account_uid IS NOT NULL
					      AND mirror.account.exch_account_uid IS DISTINCT FROM EXCLUDED.exch_account_uid)
					THEN jsonb_build_object(
						'exchange', EXCLUDED.exchange,
						'exch_account_uid', EXCLUDED.exch_account_uid,
						'account_revision', EXCLUDED.account_revision)
					ELSE mirror.account.identity_conflict
				END`,
			account.APIID, account.Name, account.Kind, nullable(account.Exchange), nullable(account.ExchAccountUID),
			account.Revision, account.DeletedAt); err != nil {
			return err
		}
	}
	return nil
}

func nullable(s string) any {
	s = strings.TrimSpace(s)
	if s == "" {
		return nil
	}
	return s
}
