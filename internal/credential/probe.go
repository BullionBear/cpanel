package credential

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BullionBear/cpanel/internal/platform/sealbox"
	"github.com/BullionBear/cpanel/internal/platform/sealbox/open"
	"github.com/BullionBear/cpanel/internal/reconcile"
	"github.com/BullionBear/cpanel/internal/venue"
	"github.com/BullionBear/cpanel/internal/worker"
)

// Probe asks the account-layer endpoint whether an auth_failed credential works
// again. A success returns it to active. It does not read kinds.
type Probe struct {
	DB        *pgxpool.Pool
	Key       *open.PrivateKey
	Endpoints Endpoints
	Open      func(exchange string, cred sealbox.Credentials) (Checker, error)
}

// Handle is the worker handler for reconcile.CredentialProbeKind.
func (p Probe) Handle(ctx context.Context, job *worker.Job) (worker.Result, error) {
	var params struct {
		CredentialID int64 `json:"credential_id"`
	}
	if job == nil || json.Unmarshal(job.Params, &params) != nil || params.CredentialID <= 0 {
		return worker.Result{}, errors.New("credential.probe: credential_id is required")
	}
	row, err := p.load(ctx, params.CredentialID)
	if err != nil {
		return worker.Result{}, err
	}
	if row.deleted || (row.status != "auth_failed" && row.status != "active") || len(row.ciphertext) == 0 {
		return worker.Result{}, nil
	}
	cred, err := open.OpenCredentials(p.Key, row.ciphertext, row.accountID, row.id)
	if err != nil {
		if row.status == "auth_failed" {
			if err := p.deferProbe(ctx, row.id, "sealed credentials are unreadable"); err != nil {
				return worker.Result{}, err
			}
		}
		return worker.Result{}, nil
	}
	client, err := openChecker(p.Endpoints, p.Open, row.exchange, cred)
	if err != nil {
		return worker.Result{}, err
	}
	check, err := client.CheckAccount(ctx)
	if err != nil {
		if row.status == "auth_failed" {
			if deferErr := p.deferProbe(ctx, row.id, ""); deferErr != nil {
				return worker.Result{Requests: client.Calls()}, deferErr
			}
		}
		return worker.Result{Requests: client.Calls()}, err
	}
	if !check.ReadOnly || check.UID != row.uid {
		if row.status == "auth_failed" {
			reason := check.Reason
			if check.UID != row.uid {
				reason = fmt.Sprintf("exchange uid %q does not match the account", check.UID)
			}
			if reason == "" {
				reason = "api key is not read-only"
			}
			if err := p.deferProbe(ctx, row.id, scrub(reason, cred)); err != nil {
				return worker.Result{Requests: client.Calls()}, err
			}
		}
		return worker.Result{Requests: client.Calls()}, nil
	}
	if err := p.succeed(ctx, row, check); err != nil {
		return worker.Result{Requests: client.Calls()}, err
	}
	return worker.Result{Requests: client.Calls()}, nil
}

func (p Probe) load(ctx context.Context, id int64) (credRow, error) {
	var row credRow
	err := p.DB.QueryRow(ctx, `
		SELECT c.id, c.exchange_account_id, a.exchange, a.exch_account_uid, c.status, c.ciphertext, a.deleted_at IS NOT NULL
		FROM pms.credential c
		JOIN pms.exchange_account a ON a.id = c.exchange_account_id
		WHERE c.id = $1`, id).Scan(&row.id, &row.accountID, &row.exchange, &row.uid, &row.status, &row.ciphertext, &row.deleted)
	if errors.Is(err, pgx.ErrNoRows) {
		return credRow{}, fmt.Errorf("credential.probe: credential %d not found", id)
	}
	return row, err
}

// deferProbe keeps an auth_failed credential failed and waits ProbeEvery before the next probe.
// An empty reason leaves the previous failure_reason in place.
func (p Probe) deferProbe(ctx context.Context, id int64, reason string) error {
	_, err := p.DB.Exec(ctx, `
		UPDATE pms.credential
		SET next_probe_at = now() + $2::interval,
		    failure_reason = CASE WHEN $3 = '' THEN failure_reason ELSE $3 END
		WHERE id = $1 AND status = 'auth_failed'`, id, ProbeEvery, reason)
	return err
}

func (p Probe) succeed(ctx context.Context, row credRow, check venue.Check) error {
	return pgx.BeginFunc(ctx, p.DB, func(tx pgx.Tx) error {
		if err := lockAccount(ctx, tx, row.accountID); err != nil {
			return err
		}
		var status string
		err := tx.QueryRow(ctx, `SELECT status FROM pms.credential WHERE id = $1 FOR UPDATE`, row.id).Scan(&status)
		if err != nil {
			return err
		}
		if status == "active" {
			_, err = tx.Exec(ctx, `
				UPDATE pms.credential SET consecutive_auth_failures = 0
				WHERE id = $1 AND status = 'active'`, row.id)
			return err
		}
		if status != "auth_failed" {
			return nil
		}
		var disabled *int64
		err = tx.QueryRow(ctx, `
			UPDATE pms.credential SET status = 'disabled'
			WHERE exchange_account_id = $1 AND status = 'active' AND id <> $2
			RETURNING id`, row.accountID, row.id).Scan(&disabled)
		if errors.Is(err, pgx.ErrNoRows) {
			disabled = nil
		} else if err != nil {
			return err
		}
		tag, err := tx.Exec(ctx, `
			UPDATE pms.credential
			SET status = 'active', verified_at = now(), failure_reason = NULL,
			    consecutive_auth_failures = 0, next_probe_at = NULL,
			    reported_uid = $2, permission_summary = NULLIF($3, '')
			WHERE id = $1 AND status = 'auth_failed' AND octet_length(ciphertext) > 0`,
			row.id, check.UID, check.Summary)
		if err != nil {
			return err
		}
		if tag.RowsAffected() != 1 {
			return nil
		}
		if err := bumpRevision(ctx, tx, row.accountID); err != nil {
			return err
		}
		params := map[string]any{"exchange_account_id": row.accountID, "credential_id": row.id, "status": "active"}
		if disabled != nil {
			params["disabled_credential_id"] = *disabled
		}
		return writeAudit(ctx, tx, "job:"+reconcile.CredentialProbeKind, "credential.probe", row.id, params)
	})
}
