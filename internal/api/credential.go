package api

import (
	"context"
	"errors"
	"net/http"
	"strconv"
	"strings"

	"github.com/jackc/pgx/v5"

	cpanelv1 "github.com/BullionBear/cpanel/contract/gen/cpanel/v1"
	"github.com/BullionBear/cpanel/internal/platform/sealbox"
	"github.com/BullionBear/cpanel/internal/reconcile"
)

const credentialCreate = "credential.create"

func (s *server) CreateExchangeAccountCredential(ctx context.Context, req cpanelv1.CreateExchangeAccountCredentialRequestObject) (cpanelv1.CreateExchangeAccountCredentialResponseObject, error) {
	p, kind := s.gate(ctx, true)
	switch kind {
	case gateUnauth:
		return cpanelv1.CreateExchangeAccountCredential401ApplicationProblemPlusJSONResponse{UnauthenticatedApplicationProblemPlusJSONResponse: unauthProblem("")}, nil
	case gateCSRF:
		return cpanelv1.CreateExchangeAccountCredential403ApplicationProblemPlusJSONResponse(forbidden("origin is not allowed")), nil
	case gateUnavailable:
		return cpanelv1.CreateExchangeAccountCredentialdefaultApplicationProblemPlusJSONResponse{Body: s.accountProblem(nil), StatusCode: http.StatusServiceUnavailable}, nil
	}
	if !s.hasSealbox {
		return cpanelv1.CreateExchangeAccountCredentialdefaultApplicationProblemPlusJSONResponse{
			Body: problemValue(http.StatusServiceUnavailable, "unavailable", "Unavailable", "sealbox public key is not configured"), StatusCode: http.StatusServiceUnavailable,
		}, nil
	}
	cred, err := s.createCredential(ctx, p.auditID(), req.Id, req.Body)
	if err != nil {
		if resp, ok := createCredentialErr(err); ok {
			return resp, nil
		}
		return cpanelv1.CreateExchangeAccountCredentialdefaultApplicationProblemPlusJSONResponse{Body: s.accountProblem(err), StatusCode: http.StatusServiceUnavailable}, nil
	}
	return cpanelv1.CreateExchangeAccountCredential202JSONResponse(cred), nil
}

func (s *server) createCredential(ctx context.Context, actor string, accountID int64, in *cpanelv1.CredentialCreate) (cpanelv1.Credential, error) {
	if in == nil {
		return cpanelv1.Credential{}, badAccount("body is required")
	}
	apiKey := strings.TrimSpace(in.ApiKey)
	secret := strings.TrimSpace(in.ApiSecret)
	passphrase := ""
	if in.Passphrase != nil {
		passphrase = strings.TrimSpace(*in.Passphrase)
	}
	if apiKey == "" || secret == "" {
		return cpanelv1.Credential{}, badAccount("api_key and api_secret are required")
	}
	if len(apiKey) < 4 {
		return cpanelv1.Credential{}, badAccount("api_key must have at least 4 characters")
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return cpanelv1.Credential{}, err
	}
	defer func() { _ = tx.Rollback(ctx) }()

	var exchange string
	var deleted bool
	err = tx.QueryRow(ctx, `
		SELECT exchange, deleted_at IS NOT NULL
		FROM pms.exchange_account
		WHERE id = $1
		FOR UPDATE`, accountID).Scan(&exchange, &deleted)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return cpanelv1.Credential{}, missingAccount("exchange account not found")
		}
		return cpanelv1.Credential{}, err
	}
	if deleted {
		return cpanelv1.Credential{}, missingAccount("exchange account not found")
	}
	if exchange == "okx" && passphrase == "" {
		return cpanelv1.Credential{}, badAccount("passphrase is required for okx")
	}
	if exchange != "okx" && passphrase != "" {
		return cpanelv1.Credential{}, badAccount("passphrase is only used for okx")
	}
	last4 := apiKey[len(apiKey)-4:]
	var id int64
	err = tx.QueryRow(ctx, `
		INSERT INTO pms.credential (exchange_account_id, purpose, status, ciphertext, api_key_last4)
		VALUES ($1, 'read', 'unverified', '\x01', $2)
		RETURNING id`, accountID, last4).Scan(&id)
	if err != nil {
		if pgCode(err) == "23505" {
			return cpanelv1.Credential{}, conflictAccount("an unverified credential already exists")
		}
		return cpanelv1.Credential{}, err
	}
	sealed, err := sealbox.SealCredentials(s.sealbox, accountID, id, sealbox.Credentials{
		APIKey: apiKey, APISecret: secret, Passphrase: passphrase,
	})
	if err != nil {
		return cpanelv1.Credential{}, err
	}
	if _, err := tx.Exec(ctx, `UPDATE pms.credential SET ciphertext = $2 WHERE id = $1`, id, sealed); err != nil {
		return cpanelv1.Credential{}, err
	}
	if _, err := tx.Exec(ctx, `
		INSERT INTO ingest.job (kind, dedup_key, lease_key, params)
		VALUES ($1, $2, $3, jsonb_build_object('credential_id', $4::bigint))`,
		reconcile.CredentialVerifyKind, reconcile.CredentialDedup(id), reconcile.AccountLease(accountID), id); err != nil {
		return cpanelv1.Credential{}, err
	}
	if err := bumpAccountRevision(ctx, tx, accountID); err != nil {
		return cpanelv1.Credential{}, err
	}
	if err := insertAudit(ctx, tx, actor, credentialCreate, "credential:"+strconv.FormatInt(id, 10), map[string]any{
		"exchange_account_id": accountID, "credential_id": id, "api_key_last4": last4, "status": "unverified",
	}, map[string]any{"status": http.StatusAccepted}); err != nil {
		return cpanelv1.Credential{}, err
	}
	if err := tx.Commit(ctx); err != nil {
		return cpanelv1.Credential{}, err
	}
	return cpanelv1.Credential{Id: id, Status: cpanelv1.Unverified, ApiKeyLast4: last4}, nil
}

func createCredentialErr(err error) (cpanelv1.CreateExchangeAccountCredentialResponseObject, bool) {
	problem, ok := accountProblemOf(err)
	if !ok {
		return nil, false
	}
	switch problem.Status {
	case http.StatusBadRequest:
		return cpanelv1.CreateExchangeAccountCredential400ApplicationProblemPlusJSONResponse(problem), true
	case http.StatusNotFound:
		return cpanelv1.CreateExchangeAccountCredential404ApplicationProblemPlusJSONResponse(problem), true
	case http.StatusConflict:
		return cpanelv1.CreateExchangeAccountCredential409ApplicationProblemPlusJSONResponse(problem), true
	default:
		return nil, false
	}
}
