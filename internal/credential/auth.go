package credential

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"

	"github.com/BullionBear/cpanel/internal/reconcile"
	"github.com/BullionBear/cpanel/internal/worker"
)

// ProbeEvery is how long reconcile waits after a probe that leaves the credential auth_failed.
const ProbeEvery = 15 * time.Minute

// ApplyAuth is the worker CredentialHook. It counts an account-layer auth failure
// on the credential named by the job. A lease that is not account:<id>, and a job
// that is not credential.verify or credential.probe, are recorded by the worker
// and do not change a credential. Two consecutive failures of an active credential
// move it to auth_failed. A kind endpoint's failure is not one of these jobs, so
// it does not.
func ApplyAuth(ctx context.Context, tx pgx.Tx, failure worker.AuthFailure) error {
	accountID, ok := accountOf(failure.LeaseKey)
	if !ok {
		return nil
	}
	if failure.Kind != reconcile.CredentialVerifyKind && failure.Kind != reconcile.CredentialProbeKind {
		return nil
	}
	if err := lockAccount(ctx, tx, accountID); err != nil {
		return err
	}
	id, ok, err := credentialID(ctx, tx, failure.JobID)
	if err != nil || !ok {
		return err
	}
	var status string
	var failures int
	var owner int64
	err = tx.QueryRow(ctx, `
		SELECT status, consecutive_auth_failures, exchange_account_id
		FROM pms.credential
		WHERE id = $1
		FOR UPDATE`, id).Scan(&status, &failures, &owner)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil
	}
	if err != nil {
		return err
	}
	if owner != accountID {
		return nil
	}
	switch status {
	case "active":
		failures++
		if failures < 2 {
			tag, err := tx.Exec(ctx, `
				UPDATE pms.credential SET consecutive_auth_failures = $2 WHERE id = $1 AND status = 'active'`,
				id, failures)
			if err != nil || tag.RowsAffected() != 1 {
				return err
			}
			return nil
		}
		reason := clipReason(failure.Message)
		tag, err := tx.Exec(ctx, `
			UPDATE pms.credential
			SET status = 'auth_failed', consecutive_auth_failures = $2, next_probe_at = now(), failure_reason = $3
			WHERE id = $1 AND status = 'active'`, id, failures, reason)
		if err != nil || tag.RowsAffected() != 1 {
			return err
		}
		if err := bumpRevision(ctx, tx, accountID); err != nil {
			return err
		}
		return writeAudit(ctx, tx, "job:"+failure.Kind, "credential.auth_failed", id, map[string]any{
			"exchange_account_id": accountID, "credential_id": id, "status": "auth_failed",
		})
	case "auth_failed":
		if failure.Kind != reconcile.CredentialProbeKind {
			return nil
		}
		_, err = tx.Exec(ctx, `
			UPDATE pms.credential SET next_probe_at = now() + $2::interval
			WHERE id = $1 AND status = 'auth_failed'`, id, ProbeEvery)
		return err
	default:
		return nil
	}
}

func accountOf(lease string) (int64, bool) {
	rest, ok := strings.CutPrefix(lease, "account:")
	if !ok || rest == "" {
		return 0, false
	}
	id, err := strconv.ParseInt(rest, 10, 64)
	if err != nil || id <= 0 {
		return 0, false
	}
	return id, true
}

func credentialID(ctx context.Context, tx pgx.Tx, jobID int64) (int64, bool, error) {
	var params []byte
	err := tx.QueryRow(ctx, `SELECT params FROM ingest.job_run WHERE id = $1`, jobID).Scan(&params)
	if errors.Is(err, pgx.ErrNoRows) {
		return 0, false, nil
	}
	if err != nil {
		return 0, false, err
	}
	var body struct {
		CredentialID int64 `json:"credential_id"`
	}
	if json.Unmarshal(params, &body) != nil || body.CredentialID <= 0 {
		return 0, false, nil
	}
	return body.CredentialID, true, nil
}

func clipReason(s string) string {
	s = strings.TrimSpace(s)
	if s == "" {
		return "exchange authentication failed"
	}
	if len(s) > maxReason {
		s = s[:maxReason]
	}
	return s
}

func lockAccount(ctx context.Context, tx pgx.Tx, accountID int64) error {
	var id int64
	err := tx.QueryRow(ctx, `SELECT id FROM pms.exchange_account WHERE id = $1 FOR UPDATE`, accountID).Scan(&id)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil
	}
	return err
}

func bumpRevision(ctx context.Context, tx pgx.Tx, accountID int64) error {
	tag, err := tx.Exec(ctx, `
		UPDATE pms.exchange_account SET revision = revision + 1
		WHERE id = $1 AND deleted_at IS NULL`, accountID)
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 1 {
		return nil
	}
	var deleted bool
	err = tx.QueryRow(ctx, `SELECT deleted_at IS NOT NULL FROM pms.exchange_account WHERE id = $1`, accountID).Scan(&deleted)
	if err != nil {
		return err
	}
	if deleted {
		return nil
	}
	return fmt.Errorf("credential: revision update affected 0 rows")
}

func writeAudit(ctx context.Context, tx pgx.Tx, actor, action string, credentialID int64, params map[string]any) error {
	raw, err := json.Marshal(params)
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `
		INSERT INTO meta.audit (actor, action, target, params, result, request_id)
		VALUES ($1, $2, $3, $4, $5, '')`,
		actor, action, fmt.Sprintf("credential:%d", credentialID), raw, []byte(`{"status":"ok"}`))
	return err
}
