package api

import (
	"context"
	"errors"
	"net/http"
	"strconv"

	"github.com/jackc/pgx/v5"

	cpanelv1 "github.com/BullionBear/cpanel/contract/gen/cpanel/v1"
	"github.com/BullionBear/cpanel/internal/reconcile"
)

func (s *server) DisableExchangeAccountCredential(ctx context.Context, req cpanelv1.DisableExchangeAccountCredentialRequestObject) (cpanelv1.DisableExchangeAccountCredentialResponseObject, error) {
	p, kind := s.gate(ctx, true)
	switch kind {
	case gateUnauth:
		return cpanelv1.DisableExchangeAccountCredential401ApplicationProblemPlusJSONResponse{UnauthenticatedApplicationProblemPlusJSONResponse: unauthProblem("")}, nil
	case gateCSRF:
		return cpanelv1.DisableExchangeAccountCredential403ApplicationProblemPlusJSONResponse(forbidden("origin is not allowed")), nil
	case gateUnavailable:
		return cpanelv1.DisableExchangeAccountCredentialdefaultApplicationProblemPlusJSONResponse{Body: s.accountProblem(nil), StatusCode: http.StatusServiceUnavailable}, nil
	}
	cred, err := s.disableCredential(ctx, p.auditID(), req.Id, req.Cid, false)
	if err != nil {
		if problem, ok := accountProblemOf(err); ok && problem.Status == http.StatusNotFound {
			return cpanelv1.DisableExchangeAccountCredential404ApplicationProblemPlusJSONResponse(problem), nil
		}
		return cpanelv1.DisableExchangeAccountCredentialdefaultApplicationProblemPlusJSONResponse{Body: s.accountProblem(err), StatusCode: http.StatusServiceUnavailable}, nil
	}
	return cpanelv1.DisableExchangeAccountCredential200JSONResponse(cred), nil
}

func (s *server) DeleteExchangeAccountCredential(ctx context.Context, req cpanelv1.DeleteExchangeAccountCredentialRequestObject) (cpanelv1.DeleteExchangeAccountCredentialResponseObject, error) {
	p, kind := s.gate(ctx, true)
	switch kind {
	case gateUnauth:
		return cpanelv1.DeleteExchangeAccountCredential401ApplicationProblemPlusJSONResponse{UnauthenticatedApplicationProblemPlusJSONResponse: unauthProblem("")}, nil
	case gateCSRF:
		return cpanelv1.DeleteExchangeAccountCredential403ApplicationProblemPlusJSONResponse(forbidden("origin is not allowed")), nil
	case gateUnavailable:
		return cpanelv1.DeleteExchangeAccountCredentialdefaultApplicationProblemPlusJSONResponse{Body: s.accountProblem(nil), StatusCode: http.StatusServiceUnavailable}, nil
	}
	cred, err := s.disableCredential(ctx, p.auditID(), req.Id, req.Cid, true)
	if err != nil {
		if problem, ok := accountProblemOf(err); ok && problem.Status == http.StatusNotFound {
			return cpanelv1.DeleteExchangeAccountCredential404ApplicationProblemPlusJSONResponse(problem), nil
		}
		return cpanelv1.DeleteExchangeAccountCredentialdefaultApplicationProblemPlusJSONResponse{Body: s.accountProblem(err), StatusCode: http.StatusServiceUnavailable}, nil
	}
	return cpanelv1.DeleteExchangeAccountCredential200JSONResponse(cred), nil
}

func (s *server) VerifyExchangeAccountCredential(ctx context.Context, req cpanelv1.VerifyExchangeAccountCredentialRequestObject) (cpanelv1.VerifyExchangeAccountCredentialResponseObject, error) {
	p, kind := s.gate(ctx, true)
	switch kind {
	case gateUnauth:
		return cpanelv1.VerifyExchangeAccountCredential401ApplicationProblemPlusJSONResponse{UnauthenticatedApplicationProblemPlusJSONResponse: unauthProblem("")}, nil
	case gateCSRF:
		return cpanelv1.VerifyExchangeAccountCredential403ApplicationProblemPlusJSONResponse(forbidden("origin is not allowed")), nil
	case gateUnavailable:
		return cpanelv1.VerifyExchangeAccountCredentialdefaultApplicationProblemPlusJSONResponse{Body: s.accountProblem(nil), StatusCode: http.StatusServiceUnavailable}, nil
	}
	cred, err := s.reverifyCredential(ctx, p.auditID(), req.Id, req.Cid)
	if err != nil {
		if resp, ok := reverifyErr(err); ok {
			return resp, nil
		}
		return cpanelv1.VerifyExchangeAccountCredentialdefaultApplicationProblemPlusJSONResponse{Body: s.accountProblem(err), StatusCode: http.StatusServiceUnavailable}, nil
	}
	return cpanelv1.VerifyExchangeAccountCredential202JSONResponse(cred), nil
}

// disableCredential sets status to disabled. wipe also clears the ciphertext.
// A second call that changes nothing does not bump revision or write audit.
// Unfinished verify and probe jobs are removed either way.
func (s *server) disableCredential(ctx context.Context, actor string, accountID, credentialID int64, wipe bool) (cpanelv1.Credential, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return cpanelv1.Credential{}, err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	if err := lockLiveAccount(ctx, tx, accountID); err != nil {
		return cpanelv1.Credential{}, err
	}
	var status string
	var length int
	err = tx.QueryRow(ctx, `
		SELECT status, octet_length(ciphertext)
		FROM pms.credential
		WHERE id = $1 AND exchange_account_id = $2
		FOR UPDATE`, credentialID, accountID).Scan(&status, &length)
	if errors.Is(err, pgx.ErrNoRows) {
		return cpanelv1.Credential{}, missingAccount("credential not found")
	}
	if err != nil {
		return cpanelv1.Credential{}, err
	}
	changed := status != "disabled" || (wipe && length > 0)
	if changed {
		query := `UPDATE pms.credential SET status = 'disabled', next_probe_at = NULL WHERE id = $1`
		if wipe {
			query = `UPDATE pms.credential SET status = 'disabled', ciphertext = ''::bytea, next_probe_at = NULL WHERE id = $1`
		}
		if _, err := tx.Exec(ctx, query, credentialID); err != nil {
			return cpanelv1.Credential{}, err
		}
	}
	if err := cancelCredentialJobs(ctx, tx, credentialID); err != nil {
		return cpanelv1.Credential{}, err
	}
	if changed {
		if err := bumpAccountRevision(ctx, tx, accountID); err != nil {
			return cpanelv1.Credential{}, err
		}
		action := "credential.disable"
		if wipe {
			action = "credential.delete"
		}
		if err := insertAudit(ctx, tx, actor, action, "credential:"+strconv.FormatInt(credentialID, 10), map[string]any{
			"exchange_account_id": accountID, "credential_id": credentialID, "status": "disabled",
		}, map[string]any{"status": http.StatusOK}); err != nil {
			return cpanelv1.Credential{}, err
		}
	}
	cred, err := loadOneCredential(ctx, tx, credentialID)
	if err != nil {
		return cpanelv1.Credential{}, err
	}
	if err := tx.Commit(ctx); err != nil {
		return cpanelv1.Credential{}, err
	}
	return cred, nil
}

func (s *server) reverifyCredential(ctx context.Context, actor string, accountID, credentialID int64) (cpanelv1.Credential, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return cpanelv1.Credential{}, err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	if err := lockLiveAccount(ctx, tx, accountID); err != nil {
		return cpanelv1.Credential{}, err
	}
	var status string
	var length int
	err = tx.QueryRow(ctx, `
		SELECT status, octet_length(ciphertext)
		FROM pms.credential
		WHERE id = $1 AND exchange_account_id = $2
		FOR UPDATE`, credentialID, accountID).Scan(&status, &length)
	if errors.Is(err, pgx.ErrNoRows) {
		return cpanelv1.Credential{}, missingAccount("credential not found")
	}
	if err != nil {
		return cpanelv1.Credential{}, err
	}
	if length == 0 || (status != "rejected" && status != "auth_failed") {
		return cpanelv1.Credential{}, conflictAccount("credential cannot be verified from its current state")
	}
	_, err = tx.Exec(ctx, `
		UPDATE pms.credential
		SET status = 'unverified', consecutive_auth_failures = 0, failure_reason = NULL, next_probe_at = NULL
		WHERE id = $1`, credentialID)
	if err != nil {
		if pgCode(err) == "23505" {
			return cpanelv1.Credential{}, conflictAccount("an unverified credential already exists")
		}
		return cpanelv1.Credential{}, err
	}
	if err := cancelCredentialJobs(ctx, tx, credentialID); err != nil {
		return cpanelv1.Credential{}, err
	}
	if _, err := tx.Exec(ctx, `
		INSERT INTO ingest.job (kind, dedup_key, lease_key, params)
		VALUES ($1, $2, $3, jsonb_build_object('credential_id', $4::bigint))`,
		reconcile.CredentialVerifyKind, reconcile.CredentialDedup(credentialID), reconcile.AccountLease(accountID), credentialID); err != nil {
		return cpanelv1.Credential{}, err
	}
	if err := bumpAccountRevision(ctx, tx, accountID); err != nil {
		return cpanelv1.Credential{}, err
	}
	if err := insertAudit(ctx, tx, actor, "credential.reverify", "credential:"+strconv.FormatInt(credentialID, 10), map[string]any{
		"exchange_account_id": accountID, "credential_id": credentialID, "status": "unverified",
	}, map[string]any{"status": http.StatusAccepted}); err != nil {
		return cpanelv1.Credential{}, err
	}
	cred, err := loadOneCredential(ctx, tx, credentialID)
	if err != nil {
		return cpanelv1.Credential{}, err
	}
	if err := tx.Commit(ctx); err != nil {
		return cpanelv1.Credential{}, err
	}
	return cred, nil
}

func lockLiveAccount(ctx context.Context, tx pgx.Tx, accountID int64) error {
	var deleted bool
	err := tx.QueryRow(ctx, `
		SELECT deleted_at IS NOT NULL FROM pms.exchange_account WHERE id = $1 FOR UPDATE`, accountID).Scan(&deleted)
	if errors.Is(err, pgx.ErrNoRows) || (err == nil && deleted) {
		return missingAccount("exchange account not found")
	}
	return err
}

func cancelCredentialJobs(ctx context.Context, tx pgx.Tx, credentialID int64) error {
	_, err := tx.Exec(ctx, `DELETE FROM ingest.job WHERE dedup_key = ANY($1)`,
		[]string{reconcile.CredentialDedup(credentialID), reconcile.CredentialProbeDedup(credentialID)})
	return err
}

func loadOneCredential(ctx context.Context, tx pgx.Tx, id int64) (cpanelv1.Credential, error) {
	var cred cpanelv1.Credential
	var status string
	err := tx.QueryRow(ctx, `
		SELECT id, status, api_key_last4, failure_reason, verified_at
		FROM pms.credential WHERE id = $1`, id).Scan(&cred.Id, &status, &cred.ApiKeyLast4, &cred.FailureReason, &cred.VerifiedAt)
	if err != nil {
		return cpanelv1.Credential{}, err
	}
	cred.Status = cpanelv1.CredentialStatus(status)
	return cred, nil
}

func reverifyErr(err error) (cpanelv1.VerifyExchangeAccountCredentialResponseObject, bool) {
	problem, ok := accountProblemOf(err)
	if !ok {
		return nil, false
	}
	switch problem.Status {
	case http.StatusNotFound:
		return cpanelv1.VerifyExchangeAccountCredential404ApplicationProblemPlusJSONResponse(problem), true
	case http.StatusConflict:
		return cpanelv1.VerifyExchangeAccountCredential409ApplicationProblemPlusJSONResponse(problem), true
	default:
		return nil, false
	}
}
