package api

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"strings"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"

	cpanelv1 "github.com/BullionBear/cpanel/contract/gen/cpanel/v1"
	"github.com/BullionBear/cpanel/internal/reconcile"
)

// Exchange accounts (C5-01). A row is one (exchange, exch_account_uid), including after
// soft delete. Kinds are membership rows: disable sets disabled_at and leaves the row.
// Credential registration is in credential.go. This file does not join mirror.account.

const (
	accountCreate     = "exchange_account.create"
	accountLabel      = "exchange_account.label"
	accountDelete     = "exchange_account.delete"
	accountKindEnable = "exchange_account.kind_enable"
	accountKindOff    = "exchange_account.kind_disable"
)

func (s *server) ListExchangeAccounts(ctx context.Context, _ cpanelv1.ListExchangeAccountsRequestObject) (cpanelv1.ListExchangeAccountsResponseObject, error) {
	if _, kind := s.gate(ctx, false); kind != gateOK {
		return listAccountsGate(kind), nil
	}
	accounts, err := listAccounts(ctx, s.pool)
	if err != nil {
		return cpanelv1.ListExchangeAccountsdefaultApplicationProblemPlusJSONResponse{Body: s.accountProblem(err), StatusCode: http.StatusServiceUnavailable}, nil
	}
	return cpanelv1.ListExchangeAccounts200JSONResponse(accounts), nil
}

func (s *server) CreateExchangeAccount(ctx context.Context, req cpanelv1.CreateExchangeAccountRequestObject) (cpanelv1.CreateExchangeAccountResponseObject, error) {
	p, kind := s.gate(ctx, true)
	switch kind {
	case gateUnauth:
		return cpanelv1.CreateExchangeAccount401ApplicationProblemPlusJSONResponse{UnauthenticatedApplicationProblemPlusJSONResponse: unauthProblem("")}, nil
	case gateCSRF:
		return cpanelv1.CreateExchangeAccount403ApplicationProblemPlusJSONResponse(forbidden("origin is not allowed")), nil
	case gateUnavailable:
		return cpanelv1.CreateExchangeAccountdefaultApplicationProblemPlusJSONResponse{Body: s.accountProblem(nil), StatusCode: http.StatusServiceUnavailable}, nil
	}
	acc, err := s.createExchangeAccount(ctx, p.auditID(), req.Body)
	if err != nil {
		if resp, ok := createAccountErr(err); ok {
			return resp, nil
		}
		return cpanelv1.CreateExchangeAccountdefaultApplicationProblemPlusJSONResponse{Body: s.accountProblem(err), StatusCode: http.StatusServiceUnavailable}, nil
	}
	return cpanelv1.CreateExchangeAccount201JSONResponse(acc), nil
}

func (s *server) GetExchangeAccount(ctx context.Context, req cpanelv1.GetExchangeAccountRequestObject) (cpanelv1.GetExchangeAccountResponseObject, error) {
	if _, kind := s.gate(ctx, false); kind != gateOK {
		return getAccountGate(kind), nil
	}
	acc, err := loadAccount(ctx, s.pool, req.Id)
	if err != nil {
		if resp, ok := getAccountErr(err); ok {
			return resp, nil
		}
		return cpanelv1.GetExchangeAccountdefaultApplicationProblemPlusJSONResponse{Body: s.accountProblem(err), StatusCode: http.StatusServiceUnavailable}, nil
	}
	return cpanelv1.GetExchangeAccount200JSONResponse(acc), nil
}

func (s *server) UpdateExchangeAccount(ctx context.Context, req cpanelv1.UpdateExchangeAccountRequestObject) (cpanelv1.UpdateExchangeAccountResponseObject, error) {
	p, kind := s.gate(ctx, true)
	switch kind {
	case gateUnauth:
		return cpanelv1.UpdateExchangeAccount401ApplicationProblemPlusJSONResponse{UnauthenticatedApplicationProblemPlusJSONResponse: unauthProblem("")}, nil
	case gateCSRF:
		return cpanelv1.UpdateExchangeAccount403ApplicationProblemPlusJSONResponse(forbidden("origin is not allowed")), nil
	case gateUnavailable:
		return cpanelv1.UpdateExchangeAccountdefaultApplicationProblemPlusJSONResponse{Body: s.accountProblem(nil), StatusCode: http.StatusServiceUnavailable}, nil
	}
	acc, err := s.updateExchangeAccount(ctx, p.auditID(), req.Id, req.Body)
	if err != nil {
		if resp, ok := updateAccountErr(err); ok {
			return resp, nil
		}
		return cpanelv1.UpdateExchangeAccountdefaultApplicationProblemPlusJSONResponse{Body: s.accountProblem(err), StatusCode: http.StatusServiceUnavailable}, nil
	}
	return cpanelv1.UpdateExchangeAccount200JSONResponse(acc), nil
}

func (s *server) DeleteExchangeAccount(ctx context.Context, req cpanelv1.DeleteExchangeAccountRequestObject) (cpanelv1.DeleteExchangeAccountResponseObject, error) {
	p, kind := s.gate(ctx, true)
	switch kind {
	case gateUnauth:
		return cpanelv1.DeleteExchangeAccount401ApplicationProblemPlusJSONResponse{UnauthenticatedApplicationProblemPlusJSONResponse: unauthProblem("")}, nil
	case gateCSRF:
		return cpanelv1.DeleteExchangeAccount403ApplicationProblemPlusJSONResponse(forbidden("origin is not allowed")), nil
	case gateUnavailable:
		return cpanelv1.DeleteExchangeAccountdefaultApplicationProblemPlusJSONResponse{Body: s.accountProblem(nil), StatusCode: http.StatusServiceUnavailable}, nil
	}
	acc, err := s.deleteExchangeAccount(ctx, p.auditID(), req.Id)
	if err != nil {
		if resp, ok := deleteAccountErr(err); ok {
			return resp, nil
		}
		return cpanelv1.DeleteExchangeAccountdefaultApplicationProblemPlusJSONResponse{Body: s.accountProblem(err), StatusCode: http.StatusServiceUnavailable}, nil
	}
	return cpanelv1.DeleteExchangeAccount200JSONResponse(acc), nil
}

func (s *server) EnableExchangeAccountKind(ctx context.Context, req cpanelv1.EnableExchangeAccountKindRequestObject) (cpanelv1.EnableExchangeAccountKindResponseObject, error) {
	p, kind := s.gate(ctx, true)
	switch kind {
	case gateUnauth:
		return cpanelv1.EnableExchangeAccountKind401ApplicationProblemPlusJSONResponse{UnauthenticatedApplicationProblemPlusJSONResponse: unauthProblem("")}, nil
	case gateCSRF:
		return cpanelv1.EnableExchangeAccountKind403ApplicationProblemPlusJSONResponse(forbidden("origin is not allowed")), nil
	case gateUnavailable:
		return cpanelv1.EnableExchangeAccountKinddefaultApplicationProblemPlusJSONResponse{Body: s.accountProblem(nil), StatusCode: http.StatusServiceUnavailable}, nil
	}
	name := ""
	if req.Body != nil {
		name = req.Body.Kind
	}
	acc, err := s.enableAccountKind(ctx, p.auditID(), req.Id, name)
	if err != nil {
		if resp, ok := enableKindErr(err); ok {
			return resp, nil
		}
		return cpanelv1.EnableExchangeAccountKinddefaultApplicationProblemPlusJSONResponse{Body: s.accountProblem(err), StatusCode: http.StatusServiceUnavailable}, nil
	}
	return cpanelv1.EnableExchangeAccountKind200JSONResponse(acc), nil
}

func (s *server) DisableExchangeAccountKind(ctx context.Context, req cpanelv1.DisableExchangeAccountKindRequestObject) (cpanelv1.DisableExchangeAccountKindResponseObject, error) {
	p, kind := s.gate(ctx, true)
	switch kind {
	case gateUnauth:
		return cpanelv1.DisableExchangeAccountKind401ApplicationProblemPlusJSONResponse{UnauthenticatedApplicationProblemPlusJSONResponse: unauthProblem("")}, nil
	case gateCSRF:
		return cpanelv1.DisableExchangeAccountKind403ApplicationProblemPlusJSONResponse(forbidden("origin is not allowed")), nil
	case gateUnavailable:
		return cpanelv1.DisableExchangeAccountKinddefaultApplicationProblemPlusJSONResponse{Body: s.accountProblem(nil), StatusCode: http.StatusServiceUnavailable}, nil
	}
	acc, err := s.disableAccountKind(ctx, p.auditID(), req.Id, req.Kind)
	if err != nil {
		if resp, ok := disableKindErr(err); ok {
			return resp, nil
		}
		return cpanelv1.DisableExchangeAccountKinddefaultApplicationProblemPlusJSONResponse{Body: s.accountProblem(err), StatusCode: http.StatusServiceUnavailable}, nil
	}
	return cpanelv1.DisableExchangeAccountKind200JSONResponse(acc), nil
}

type accountDB interface {
	Query(ctx context.Context, sql string, args ...any) (pgx.Rows, error)
	QueryRow(ctx context.Context, sql string, args ...any) pgx.Row
	Exec(ctx context.Context, sql string, args ...any) (pgconn.CommandTag, error)
}

type accountStatus struct {
	status int
	detail string
}

func (e *accountStatus) Error() string { return e.detail }

func badAccount(detail string) error {
	return &accountStatus{status: http.StatusBadRequest, detail: detail}
}

func missingAccount(detail string) error {
	return &accountStatus{status: http.StatusNotFound, detail: detail}
}

func conflictAccount(detail string) error {
	return &accountStatus{status: http.StatusConflict, detail: detail}
}

func (s *server) createExchangeAccount(ctx context.Context, actor string, in *cpanelv1.ExchangeAccountCreate) (cpanelv1.ExchangeAccount, error) {
	if in == nil {
		return cpanelv1.ExchangeAccount{}, badAccount("body is required")
	}
	exchange := strings.TrimSpace(string(in.Exchange))
	if !knownExchange(exchange) {
		return cpanelv1.ExchangeAccount{}, badAccount("exchange must be binance, bybit, or okx")
	}
	uid := strings.TrimSpace(in.ExchAccountUid)
	if uid == "" {
		return cpanelv1.ExchangeAccount{}, badAccount("exch_account_uid is required")
	}
	label := strings.TrimSpace(in.Label)
	if label == "" {
		return cpanelv1.ExchangeAccount{}, badAccount("label is required")
	}
	var requested []string
	if in.Kinds != nil {
		var err error
		requested, err = dedupeKinds(*in.Kinds)
		if err != nil {
			return cpanelv1.ExchangeAccount{}, err
		}
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return cpanelv1.ExchangeAccount{}, err
	}
	defer func() { _ = tx.Rollback(ctx) }()

	var id int64
	err = tx.QueryRow(ctx, `
		INSERT INTO pms.exchange_account (exchange, exch_account_uid, label)
		VALUES ($1, $2, $3)
		RETURNING id`, exchange, uid, label).Scan(&id)
	if err != nil {
		if pgCode(err) == "23505" {
			return cpanelv1.ExchangeAccount{}, conflictAccount("exchange account already exists")
		}
		if isConstraint(err) {
			return cpanelv1.ExchangeAccount{}, badAccount("exchange account is not valid")
		}
		return cpanelv1.ExchangeAccount{}, err
	}
	kinds, err := resolveCreateKinds(ctx, tx, exchange, requested)
	if err != nil {
		return cpanelv1.ExchangeAccount{}, err
	}
	for _, kind := range kinds {
		if _, err := tx.Exec(ctx, `
			INSERT INTO pms.exchange_account_kind (exchange_account_id, kind)
			VALUES ($1, $2)`, id, kind); err != nil {
			if isConstraint(err) {
				return cpanelv1.ExchangeAccount{}, badAccount("kind is not in scope for this exchange")
			}
			return cpanelv1.ExchangeAccount{}, err
		}
	}
	if err := insertAudit(ctx, tx, actor, accountCreate, accountTarget(id), map[string]any{
		"exchange": exchange, "exch_account_uid": uid, "label": label, "kinds": kinds,
	}, map[string]any{"status": http.StatusCreated}); err != nil {
		return cpanelv1.ExchangeAccount{}, err
	}
	acc, err := loadAccount(ctx, tx, id)
	if err != nil {
		return cpanelv1.ExchangeAccount{}, err
	}
	if err := tx.Commit(ctx); err != nil {
		return cpanelv1.ExchangeAccount{}, err
	}
	return acc, nil
}

func (s *server) updateExchangeAccount(ctx context.Context, actor string, id int64, in *cpanelv1.ExchangeAccountUpdate) (cpanelv1.ExchangeAccount, error) {
	if in == nil {
		return cpanelv1.ExchangeAccount{}, badAccount("body is required")
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return cpanelv1.ExchangeAccount{}, err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	acc, err := lockAccount(ctx, tx, id)
	if err != nil {
		return cpanelv1.ExchangeAccount{}, err
	}
	if acc.DeletedAt != nil {
		return cpanelv1.ExchangeAccount{}, conflictAccount("exchange account is deleted")
	}
	if in.ExchAccountUid != nil && strings.TrimSpace(*in.ExchAccountUid) != acc.ExchAccountUid {
		return cpanelv1.ExchangeAccount{}, badAccount("exch_account_uid cannot be changed")
	}
	if in.Label != nil {
		label := strings.TrimSpace(*in.Label)
		if label == "" {
			return cpanelv1.ExchangeAccount{}, badAccount("label is required")
		}
		if label != acc.Label {
			tag, err := tx.Exec(ctx, `
				UPDATE pms.exchange_account
				SET label = $2, revision = revision + 1
				WHERE id = $1 AND deleted_at IS NULL`, id, label)
			if err != nil {
				return cpanelv1.ExchangeAccount{}, err
			}
			if tag.RowsAffected() != 1 {
				return cpanelv1.ExchangeAccount{}, fmt.Errorf("label update affected %d rows", tag.RowsAffected())
			}
			if err := insertAudit(ctx, tx, actor, accountLabel, accountTarget(id),
				map[string]any{"label": label}, map[string]any{"status": http.StatusOK}); err != nil {
				return cpanelv1.ExchangeAccount{}, err
			}
		}
	}
	acc, err = loadAccount(ctx, tx, id)
	if err != nil {
		return cpanelv1.ExchangeAccount{}, err
	}
	if err := tx.Commit(ctx); err != nil {
		return cpanelv1.ExchangeAccount{}, err
	}
	return acc, nil
}

func (s *server) deleteExchangeAccount(ctx context.Context, actor string, id int64) (cpanelv1.ExchangeAccount, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return cpanelv1.ExchangeAccount{}, err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	acc, err := lockAccount(ctx, tx, id)
	if err != nil {
		return cpanelv1.ExchangeAccount{}, err
	}
	if acc.DeletedAt == nil {
		tag, err := tx.Exec(ctx, `
			UPDATE pms.exchange_account SET deleted_at = now()
			WHERE id = $1 AND deleted_at IS NULL`, id)
		if err != nil {
			return cpanelv1.ExchangeAccount{}, err
		}
		if tag.RowsAffected() != 1 {
			return cpanelv1.ExchangeAccount{}, fmt.Errorf("delete affected %d rows", tag.RowsAffected())
		}
		if err := insertAudit(ctx, tx, actor, accountDelete, accountTarget(id), nil, map[string]any{"status": http.StatusOK}); err != nil {
			return cpanelv1.ExchangeAccount{}, err
		}
		if err := retireAccountCredentials(ctx, tx, actor, id); err != nil {
			return cpanelv1.ExchangeAccount{}, err
		}
		acc, err = loadAccount(ctx, tx, id)
		if err != nil {
			return cpanelv1.ExchangeAccount{}, err
		}
	}
	if err := tx.Commit(ctx); err != nil {
		return cpanelv1.ExchangeAccount{}, err
	}
	return acc, nil
}

// retireAccountCredentials disables every credential of a deleted account and clears
// its ciphertext, the same way DELETE of one credential does. Kind rows stay.
// The account's revision is left alone: deleting the account does not bump it.
func retireAccountCredentials(ctx context.Context, tx pgx.Tx, actor string, accountID int64) error {
	rows, err := tx.Query(ctx, `
		SELECT id FROM pms.credential
		WHERE exchange_account_id = $1 AND (status <> 'disabled' OR octet_length(ciphertext) > 0)
		ORDER BY id
		FOR UPDATE`, accountID)
	if err != nil {
		return err
	}
	var ids []int64
	for rows.Next() {
		var id int64
		if err := rows.Scan(&id); err != nil {
			rows.Close()
			return err
		}
		ids = append(ids, id)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return err
	}
	if len(ids) == 0 {
		return nil
	}
	if _, err := tx.Exec(ctx, `
		UPDATE pms.credential
		SET status = 'disabled', ciphertext = ''::bytea, next_probe_at = NULL
		WHERE id = ANY($1)`, ids); err != nil {
		return err
	}
	dedups := make([]string, 0, len(ids)*2)
	for _, id := range ids {
		dedups = append(dedups, reconcile.CredentialDedup(id), reconcile.CredentialProbeDedup(id))
		if err := insertAudit(ctx, tx, actor, "credential.delete", "credential:"+strconv.FormatInt(id, 10), map[string]any{
			"exchange_account_id": accountID, "credential_id": id, "status": "disabled",
		}, map[string]any{"status": http.StatusOK}); err != nil {
			return err
		}
	}
	_, err = tx.Exec(ctx, `DELETE FROM ingest.job WHERE dedup_key = ANY($1)`, dedups)
	return err
}

func (s *server) enableAccountKind(ctx context.Context, actor string, id int64, kind string) (cpanelv1.ExchangeAccount, error) {
	kind = strings.TrimSpace(kind)
	if kind == "" {
		return cpanelv1.ExchangeAccount{}, badAccount("kind is required")
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return cpanelv1.ExchangeAccount{}, err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	acc, err := lockAccount(ctx, tx, id)
	if err != nil {
		return cpanelv1.ExchangeAccount{}, err
	}
	if acc.DeletedAt != nil {
		return cpanelv1.ExchangeAccount{}, conflictAccount("exchange account is deleted")
	}
	if err := kindAllowed(ctx, tx, string(acc.Exchange), kind); err != nil {
		return cpanelv1.ExchangeAccount{}, err
	}
	var disabled *bool
	err = tx.QueryRow(ctx, `
		SELECT disabled_at IS NOT NULL
		FROM pms.exchange_account_kind
		WHERE exchange_account_id = $1 AND kind = $2
		FOR UPDATE`, id, kind).Scan(&disabled)
	switch {
	case errors.Is(err, pgx.ErrNoRows):
		if _, err := tx.Exec(ctx, `
			INSERT INTO pms.exchange_account_kind (exchange_account_id, kind)
			VALUES ($1, $2)`, id, kind); err != nil {
			if isConstraint(err) {
				return cpanelv1.ExchangeAccount{}, badAccount("kind is not in scope for this exchange")
			}
			return cpanelv1.ExchangeAccount{}, err
		}
		if err := bumpAccountRevision(ctx, tx, id); err != nil {
			return cpanelv1.ExchangeAccount{}, err
		}
		if err := insertAudit(ctx, tx, actor, accountKindEnable, accountTarget(id),
			map[string]any{"kind": kind}, map[string]any{"status": http.StatusOK}); err != nil {
			return cpanelv1.ExchangeAccount{}, err
		}
	case err != nil:
		return cpanelv1.ExchangeAccount{}, err
	case disabled != nil && *disabled:
		if _, err := tx.Exec(ctx, `
			UPDATE pms.exchange_account_kind
			SET disabled_at = NULL, verified_at = NULL, last_error = NULL
			WHERE exchange_account_id = $1 AND kind = $2`, id, kind); err != nil {
			return cpanelv1.ExchangeAccount{}, err
		}
		if err := bumpAccountRevision(ctx, tx, id); err != nil {
			return cpanelv1.ExchangeAccount{}, err
		}
		if err := insertAudit(ctx, tx, actor, accountKindEnable, accountTarget(id),
			map[string]any{"kind": kind}, map[string]any{"status": http.StatusOK}); err != nil {
			return cpanelv1.ExchangeAccount{}, err
		}
	}
	acc, err = loadAccount(ctx, tx, id)
	if err != nil {
		return cpanelv1.ExchangeAccount{}, err
	}
	if err := tx.Commit(ctx); err != nil {
		return cpanelv1.ExchangeAccount{}, err
	}
	return acc, nil
}

func (s *server) disableAccountKind(ctx context.Context, actor string, id int64, kind string) (cpanelv1.ExchangeAccount, error) {
	kind = strings.TrimSpace(kind)
	if kind == "" {
		return cpanelv1.ExchangeAccount{}, badAccount("kind is required")
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return cpanelv1.ExchangeAccount{}, err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	acc, err := lockAccount(ctx, tx, id)
	if err != nil {
		return cpanelv1.ExchangeAccount{}, err
	}
	if acc.DeletedAt != nil {
		return cpanelv1.ExchangeAccount{}, conflictAccount("exchange account is deleted")
	}
	var disabled bool
	err = tx.QueryRow(ctx, `
		SELECT disabled_at IS NOT NULL
		FROM pms.exchange_account_kind
		WHERE exchange_account_id = $1 AND kind = $2
		FOR UPDATE`, id, kind).Scan(&disabled)
	if errors.Is(err, pgx.ErrNoRows) {
		return cpanelv1.ExchangeAccount{}, missingAccount("kind is not on this account")
	}
	if err != nil {
		return cpanelv1.ExchangeAccount{}, err
	}
	if !disabled {
		if _, err := tx.Exec(ctx, `
			UPDATE pms.exchange_account_kind
			SET disabled_at = now()
			WHERE exchange_account_id = $1 AND kind = $2 AND disabled_at IS NULL`, id, kind); err != nil {
			return cpanelv1.ExchangeAccount{}, err
		}
		if err := bumpAccountRevision(ctx, tx, id); err != nil {
			return cpanelv1.ExchangeAccount{}, err
		}
		if err := insertAudit(ctx, tx, actor, accountKindOff, accountTarget(id),
			map[string]any{"kind": kind}, map[string]any{"status": http.StatusOK}); err != nil {
			return cpanelv1.ExchangeAccount{}, err
		}
	}
	acc, err = loadAccount(ctx, tx, id)
	if err != nil {
		return cpanelv1.ExchangeAccount{}, err
	}
	if err := tx.Commit(ctx); err != nil {
		return cpanelv1.ExchangeAccount{}, err
	}
	return acc, nil
}

func resolveCreateKinds(ctx context.Context, q accountDB, exchange string, requested []string) ([]string, error) {
	if len(requested) == 0 {
		if exchange != "bybit" && exchange != "okx" {
			return []string{}, nil
		}
		rows, err := q.Query(ctx, `
			SELECT kind FROM pms.account_kind
			WHERE exchange = $1 AND in_scope
			ORDER BY kind`, exchange)
		if err != nil {
			return nil, err
		}
		defer rows.Close()
		var kinds []string
		for rows.Next() {
			var kind string
			if err := rows.Scan(&kind); err != nil {
				return nil, err
			}
			kinds = append(kinds, kind)
		}
		if err := rows.Err(); err != nil {
			return nil, err
		}
		if len(kinds) != 1 {
			return nil, fmt.Errorf("%s has %d in-scope kinds", exchange, len(kinds))
		}
		return kinds, nil
	}
	for _, kind := range requested {
		if err := kindAllowed(ctx, q, exchange, kind); err != nil {
			return nil, err
		}
	}
	return requested, nil
}

func kindAllowed(ctx context.Context, q accountDB, exchange, kind string) error {
	var kindExchange string
	var inScope bool
	err := q.QueryRow(ctx, `SELECT exchange, in_scope FROM pms.account_kind WHERE kind = $1`, kind).Scan(&kindExchange, &inScope)
	if errors.Is(err, pgx.ErrNoRows) {
		return badAccount("unknown kind")
	}
	if err != nil {
		return err
	}
	if kindExchange != exchange || !inScope {
		return badAccount("kind is not in scope for this exchange")
	}
	return nil
}

func dedupeKinds(in []string) ([]string, error) {
	seen := map[string]struct{}{}
	out := make([]string, 0, len(in))
	for _, kind := range in {
		kind = strings.TrimSpace(kind)
		if kind == "" {
			return nil, badAccount("kind is required")
		}
		if _, ok := seen[kind]; ok {
			continue
		}
		seen[kind] = struct{}{}
		out = append(out, kind)
	}
	return out, nil
}

func listAccounts(ctx context.Context, q accountDB) ([]cpanelv1.ExchangeAccount, error) {
	rows, err := q.Query(ctx, `
		SELECT id, exchange, exch_account_uid, label, revision, deleted_at
		FROM pms.exchange_account
		WHERE deleted_at IS NULL
		ORDER BY id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := make([]cpanelv1.ExchangeAccount, 0)
	var ids []int64
	for rows.Next() {
		acc, err := scanAccount(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, acc)
		ids = append(ids, acc.Id)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	kinds, err := loadKinds(ctx, q, ids)
	if err != nil {
		return nil, err
	}
	creds, err := loadCredentials(ctx, q, ids)
	if err != nil {
		return nil, err
	}
	mirrored, err := loadMirroredAccounts(ctx, q, ids)
	if err != nil {
		return nil, err
	}
	for i := range out {
		out[i].Kinds = kinds[out[i].Id]
		if out[i].Kinds == nil {
			out[i].Kinds = []cpanelv1.ExchangeAccountKind{}
		}
		c := creds[out[i].Id]
		if c == nil {
			c = []cpanelv1.Credential{}
		}
		out[i].Credentials = &c
		m := mirrored[out[i].Id]
		if m == nil {
			m = []cpanelv1.MirroredAccount{}
		}
		out[i].ApiIds = &m
	}
	return out, nil
}

func lockAccount(ctx context.Context, q accountDB, id int64) (cpanelv1.ExchangeAccount, error) {
	row := q.QueryRow(ctx, `
		SELECT id, exchange, exch_account_uid, label, revision, deleted_at
		FROM pms.exchange_account
		WHERE id = $1
		FOR UPDATE`, id)
	acc, err := scanAccount(row)
	if errors.Is(err, pgx.ErrNoRows) {
		return cpanelv1.ExchangeAccount{}, missingAccount("exchange account not found")
	}
	if err != nil {
		return cpanelv1.ExchangeAccount{}, err
	}
	acc.Kinds = []cpanelv1.ExchangeAccountKind{}
	return acc, nil
}

func loadAccount(ctx context.Context, q accountDB, id int64) (cpanelv1.ExchangeAccount, error) {
	row := q.QueryRow(ctx, `
		SELECT id, exchange, exch_account_uid, label, revision, deleted_at
		FROM pms.exchange_account
		WHERE id = $1`, id)
	acc, err := scanAccount(row)
	if errors.Is(err, pgx.ErrNoRows) {
		return cpanelv1.ExchangeAccount{}, missingAccount("exchange account not found")
	}
	if err != nil {
		return cpanelv1.ExchangeAccount{}, err
	}
	kinds, err := loadKinds(ctx, q, []int64{id})
	if err != nil {
		return cpanelv1.ExchangeAccount{}, err
	}
	acc.Kinds = kinds[id]
	if acc.Kinds == nil {
		acc.Kinds = []cpanelv1.ExchangeAccountKind{}
	}
	creds, err := loadCredentials(ctx, q, []int64{id})
	if err != nil {
		return cpanelv1.ExchangeAccount{}, err
	}
	c := creds[id]
	if c == nil {
		c = []cpanelv1.Credential{}
	}
	acc.Credentials = &c
	mirrored, err := loadMirroredAccounts(ctx, q, []int64{id})
	if err != nil {
		return cpanelv1.ExchangeAccount{}, err
	}
	m := mirrored[id]
	if m == nil {
		m = []cpanelv1.MirroredAccount{}
	}
	acc.ApiIds = &m
	return acc, nil
}

func scanAccount(row interface{ Scan(...any) error }) (cpanelv1.ExchangeAccount, error) {
	var acc cpanelv1.ExchangeAccount
	var exchange string
	err := row.Scan(&acc.Id, &exchange, &acc.ExchAccountUid, &acc.Label, &acc.Revision, &acc.DeletedAt)
	acc.Exchange = cpanelv1.Exchange(exchange)
	return acc, err
}

func loadKinds(ctx context.Context, q accountDB, ids []int64) (map[int64][]cpanelv1.ExchangeAccountKind, error) {
	out := map[int64][]cpanelv1.ExchangeAccountKind{}
	if len(ids) == 0 {
		return out, nil
	}
	rows, err := q.Query(ctx, `
		SELECT exchange_account_id, kind, disabled_at, verified_at, last_error
		FROM pms.exchange_account_kind
		WHERE exchange_account_id = ANY($1)
		ORDER BY kind`, ids)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var id int64
		var kind cpanelv1.ExchangeAccountKind
		if err := rows.Scan(&id, &kind.Kind, &kind.DisabledAt, &kind.VerifiedAt, &kind.LastError); err != nil {
			return nil, err
		}
		out[id] = append(out[id], kind)
	}
	return out, rows.Err()
}

func loadCredentials(ctx context.Context, q accountDB, ids []int64) (map[int64][]cpanelv1.Credential, error) {
	out := map[int64][]cpanelv1.Credential{}
	if len(ids) == 0 {
		return out, nil
	}
	rows, err := q.Query(ctx, `
		SELECT id, exchange_account_id, status, api_key_last4, failure_reason, verified_at
		FROM pms.credential
		WHERE exchange_account_id = ANY($1)
		ORDER BY id`, ids)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var accountID int64
		var cred cpanelv1.Credential
		var status string
		if err := rows.Scan(&cred.Id, &accountID, &status, &cred.ApiKeyLast4, &cred.FailureReason, &cred.VerifiedAt); err != nil {
			return nil, err
		}
		cred.Status = cpanelv1.CredentialStatus(status)
		out[accountID] = append(out[accountID], cred)
	}
	return out, rows.Err()
}

func bumpAccountRevision(ctx context.Context, q accountDB, id int64) error {
	tag, err := q.Exec(ctx, `
		UPDATE pms.exchange_account
		SET revision = revision + 1
		WHERE id = $1 AND deleted_at IS NULL`, id)
	if err != nil {
		return err
	}
	if tag.RowsAffected() != 1 {
		return fmt.Errorf("revision update affected %d rows", tag.RowsAffected())
	}
	return nil
}

func insertAudit(ctx context.Context, q accountDB, actor, action, target string, params, result any) error {
	p, err := jsonOrNil(params)
	if err != nil {
		return err
	}
	r, err := jsonOrNil(result)
	if err != nil {
		return err
	}
	_, err = q.Exec(ctx, `
		INSERT INTO meta.audit (actor, action, target, params, result, request_id)
		VALUES ($1, $2, $3, $4, $5, $6)`,
		actor, action, target, p, r, requestIDFrom(ctx))
	return err
}

func jsonOrNil(v any) ([]byte, error) {
	if v == nil {
		return nil, nil
	}
	return json.Marshal(v)
}

func accountTarget(id int64) string { return fmt.Sprintf("exchange_account:%d", id) }

func knownExchange(exchange string) bool {
	switch exchange {
	case "binance", "bybit", "okx":
		return true
	default:
		return false
	}
}

func pgCode(err error) string {
	var pgErr *pgconn.PgError
	if errors.As(err, &pgErr) {
		return pgErr.Code
	}
	return ""
}

func isConstraint(err error) bool {
	switch pgCode(err) {
	case "23514", "23503", "23502":
		return true
	default:
		return false
	}
}

func (s *server) accountProblem(err error) cpanelv1.Problem {
	if err != nil {
		s.log.Error().Err(err).Msg("exchange account")
	}
	return problemValue(http.StatusServiceUnavailable, "unavailable", "Unavailable", "")
}

func accountProblemOf(err error) (cpanelv1.Problem, bool) {
	var st *accountStatus
	if !errors.As(err, &st) {
		return cpanelv1.Problem{}, false
	}
	switch st.status {
	case http.StatusBadRequest:
		return problemValue(st.status, "bad_request", "Bad Request", st.detail), true
	case http.StatusNotFound:
		return problemValue(st.status, "not_found", "Not Found", st.detail), true
	case http.StatusConflict:
		return problemValue(st.status, "conflict", "Conflict", st.detail), true
	default:
		return cpanelv1.Problem{}, false
	}
}

func listAccountsGate(kind int) cpanelv1.ListExchangeAccountsResponseObject {
	switch kind {
	case gateUnauth:
		return cpanelv1.ListExchangeAccounts401ApplicationProblemPlusJSONResponse{UnauthenticatedApplicationProblemPlusJSONResponse: unauthProblem("")}
	default:
		return cpanelv1.ListExchangeAccountsdefaultApplicationProblemPlusJSONResponse{
			Body: problemValue(http.StatusServiceUnavailable, "unavailable", "Unavailable", ""), StatusCode: http.StatusServiceUnavailable,
		}
	}
}

func getAccountGate(kind int) cpanelv1.GetExchangeAccountResponseObject {
	switch kind {
	case gateUnauth:
		return cpanelv1.GetExchangeAccount401ApplicationProblemPlusJSONResponse{UnauthenticatedApplicationProblemPlusJSONResponse: unauthProblem("")}
	default:
		return cpanelv1.GetExchangeAccountdefaultApplicationProblemPlusJSONResponse{
			Body: problemValue(http.StatusServiceUnavailable, "unavailable", "Unavailable", ""), StatusCode: http.StatusServiceUnavailable,
		}
	}
}

func createAccountErr(err error) (cpanelv1.CreateExchangeAccountResponseObject, bool) {
	p, ok := accountProblemOf(err)
	if !ok {
		return nil, false
	}
	switch p.Status {
	case http.StatusBadRequest:
		return cpanelv1.CreateExchangeAccount400ApplicationProblemPlusJSONResponse(p), true
	case http.StatusConflict:
		return cpanelv1.CreateExchangeAccount409ApplicationProblemPlusJSONResponse(p), true
	default:
		return nil, false
	}
}

func getAccountErr(err error) (cpanelv1.GetExchangeAccountResponseObject, bool) {
	p, ok := accountProblemOf(err)
	if !ok || p.Status != http.StatusNotFound {
		return nil, false
	}
	return cpanelv1.GetExchangeAccount404ApplicationProblemPlusJSONResponse(p), true
}

func updateAccountErr(err error) (cpanelv1.UpdateExchangeAccountResponseObject, bool) {
	p, ok := accountProblemOf(err)
	if !ok {
		return nil, false
	}
	switch p.Status {
	case http.StatusBadRequest:
		return cpanelv1.UpdateExchangeAccount400ApplicationProblemPlusJSONResponse(p), true
	case http.StatusNotFound:
		return cpanelv1.UpdateExchangeAccount404ApplicationProblemPlusJSONResponse(p), true
	case http.StatusConflict:
		return cpanelv1.UpdateExchangeAccount409ApplicationProblemPlusJSONResponse(p), true
	default:
		return nil, false
	}
}

func deleteAccountErr(err error) (cpanelv1.DeleteExchangeAccountResponseObject, bool) {
	p, ok := accountProblemOf(err)
	if !ok || p.Status != http.StatusNotFound {
		return nil, false
	}
	return cpanelv1.DeleteExchangeAccount404ApplicationProblemPlusJSONResponse(p), true
}

func enableKindErr(err error) (cpanelv1.EnableExchangeAccountKindResponseObject, bool) {
	p, ok := accountProblemOf(err)
	if !ok {
		return nil, false
	}
	switch p.Status {
	case http.StatusBadRequest:
		return cpanelv1.EnableExchangeAccountKind400ApplicationProblemPlusJSONResponse(p), true
	case http.StatusNotFound:
		return cpanelv1.EnableExchangeAccountKind404ApplicationProblemPlusJSONResponse(p), true
	case http.StatusConflict:
		return cpanelv1.EnableExchangeAccountKind409ApplicationProblemPlusJSONResponse(p), true
	default:
		return nil, false
	}
}

func disableKindErr(err error) (cpanelv1.DisableExchangeAccountKindResponseObject, bool) {
	p, ok := accountProblemOf(err)
	if !ok {
		return nil, false
	}
	switch p.Status {
	case http.StatusNotFound:
		return cpanelv1.DisableExchangeAccountKind404ApplicationProblemPlusJSONResponse(p), true
	case http.StatusConflict:
		return cpanelv1.DisableExchangeAccountKind409ApplicationProblemPlusJSONResponse(p), true
	default:
		return nil, false
	}
}
