package api

import (
	"context"
	"encoding/json"
	"net/http"

	cpanelv1 "github.com/BullionBear/cpanel/contract/gen/cpanel/v1"
)

// Coverage is the SQL views in migration 0010. This handler only reads them.

func (s *server) ListUnmatchedAccounts(ctx context.Context, req cpanelv1.ListUnmatchedAccountsRequestObject) (cpanelv1.ListUnmatchedAccountsResponseObject, error) {
	if _, kind := s.gate(ctx, false); kind != gateOK {
		return listUnmatchedGate(kind), nil
	}
	if req.Params.Reason != nil && !req.Params.Reason.Valid() {
		p := problemValue(http.StatusBadRequest, "bad_request", "Bad Request", "reason is not valid")
		return cpanelv1.ListUnmatchedAccounts400ApplicationProblemPlusJSONResponse(p), nil
	}
	rows, err := loadUnmatched(ctx, s.pool, req.Params.Reason)
	if err != nil {
		return cpanelv1.ListUnmatchedAccountsdefaultApplicationProblemPlusJSONResponse{
			Body: s.accountProblem(err), StatusCode: http.StatusServiceUnavailable,
		}, nil
	}
	return cpanelv1.ListUnmatchedAccounts200JSONResponse(rows), nil
}

func loadUnmatched(ctx context.Context, q accountDB, reason *cpanelv1.UnmatchedReason) ([]cpanelv1.UnmatchedAccount, error) {
	var filter *string
	if reason != nil {
		s := string(*reason)
		filter = &s
	}
	rows, err := q.Query(ctx, `
		SELECT exchange, exch_account_uid, api_ids, missing_kinds, traded, scope_count, since, reason, first_seen_at
		FROM pms.unmatched
		WHERE $1::text IS NULL OR reason = $1
		ORDER BY reason, exchange NULLS FIRST, exch_account_uid NULLS FIRST, kind NULLS FIRST, first_seen_at`, filter)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []cpanelv1.UnmatchedAccount{}
	for rows.Next() {
		var row cpanelv1.UnmatchedAccount
		var raw []byte
		var reasonText string
		if err := rows.Scan(&row.Exchange, &row.ExchAccountUid, &raw, &row.MissingKinds, &row.Traded, &row.ScopeCount, &row.Since, &reasonText, &row.FirstSeenAt); err != nil {
			return nil, err
		}
		if err := json.Unmarshal(raw, &row.ApiIds); err != nil {
			return nil, err
		}
		if row.ApiIds == nil {
			row.ApiIds = []cpanelv1.MirroredAccount{}
		}
		if row.MissingKinds == nil {
			row.MissingKinds = []string{}
		}
		row.Reason = cpanelv1.UnmatchedReason(reasonText)
		out = append(out, row)
	}
	return out, rows.Err()
}

func loadMirroredAccounts(ctx context.Context, q accountDB, ids []int64) (map[int64][]cpanelv1.MirroredAccount, error) {
	out := map[int64][]cpanelv1.MirroredAccount{}
	if len(ids) == 0 {
		return out, nil
	}
	rows, err := q.Query(ctx, `
		SELECT a.id, m.api_id, m.name, m.kind, m.deleted_at
		FROM pms.exchange_account a
		JOIN mirror.account m
		  ON m.exchange = a.exchange
		 AND m.exch_account_uid = a.exch_account_uid
		WHERE a.id = ANY($1)
		  AND a.deleted_at IS NULL
		ORDER BY m.api_id`, ids)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var id int64
		var row cpanelv1.MirroredAccount
		if err := rows.Scan(&id, &row.ApiId, &row.Name, &row.Kind, &row.DeletedAt); err != nil {
			return nil, err
		}
		out[id] = append(out[id], row)
	}
	return out, rows.Err()
}

func listUnmatchedGate(kind int) cpanelv1.ListUnmatchedAccountsResponseObject {
	switch kind {
	case gateUnauth:
		return cpanelv1.ListUnmatchedAccounts401ApplicationProblemPlusJSONResponse{UnauthenticatedApplicationProblemPlusJSONResponse: unauthProblem("")}
	default:
		return cpanelv1.ListUnmatchedAccountsdefaultApplicationProblemPlusJSONResponse{
			Body: problemValue(http.StatusServiceUnavailable, "unavailable", "Unavailable", ""), StatusCode: http.StatusServiceUnavailable,
		}
	}
}
