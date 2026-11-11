package api

import (
	"context"
	"encoding/json"
	"net/http"
	"strconv"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/rs/zerolog"

	"github.com/BullionBear/cpanel/internal/auth"
	"github.com/BullionBear/cpanel/internal/platform/pgtest"
	"github.com/BullionBear/cpanel/migrations"
)

func TestCoverage(t *testing.T) {
	db := pgtest.Start(t)
	ctx := context.Background()
	if _, err := migrations.Up(ctx, db.DSN()); err != nil {
		t.Fatal(err)
	}
	pool := poolFor(t, db.DSN())
	h := Handler(Config{Log: zerolog.Nop(), Pool: pool, Auth: testAuthSettings()})
	raw := "cpanel_coverage_test"
	if _, err := (&auth.Store{Pool: pool}).CreateToken(ctx, "79", "ada", "coverage", raw); err != nil {
		t.Fatal(err)
	}
	authz := bearerHeader(raw)
	seen := time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC)
	earlier := seen.Add(-time.Hour)
	since := time.Date(2024, 6, 1, 0, 0, 0, 0, time.UTC)

	problemOf(t, call(h, http.MethodGet, "/exchange-accounts/unmatched", "", nil), http.StatusUnauthorized, "unauthenticated")
	problemOf(t, call(h, http.MethodGet, "/exchange-accounts/unmatched?reason=nope", "", authz), http.StatusBadRequest, "bad_request")

	insertMirror(t, ctx, pool, 1, "spot", "Binance", "binance", "uid-1", false, earlier)
	insertMirror(t, ctx, pool, 2, "um", "BinanceUM", "binance", "uid-1", false, seen)
	insertScope(t, ctx, pool, 1, 10, since)
	insertMirror(t, ctx, pool, 3, "pm", "BinancePM", "binance", "uid-pm", false, seen)
	insertScope(t, ctx, pool, 3, 11, since)
	insertMirror(t, ctx, pool, 4, "eo", "BinanceEO", "binance", "uid-eo", false, seen)
	insertMirror(t, ctx, pool, 5, "mix-um", "BinanceUM", "binance", "uid-mix", false, seen)
	insertMirror(t, ctx, pool, 6, "mix-cm", "BinanceCM", "binance", "uid-mix", false, seen)
	insertMirror(t, ctx, pool, 7, "mix-eo", "BinanceEO", "binance", "uid-mix", false, seen)
	insertMirror(t, ctx, pool, 8, "nope", "Nope", "binance", "uid-nope", false, seen)
	insertMirror(t, ctx, pool, 9, "gone", "Binance", "binance", "uid-gone", true, seen)
	insertMirror(t, ctx, pool, 10, "dead", "Binance", "binance", "uid-dead", true, seen)
	insertScope(t, ctx, pool, 10, 12, since)
	insertMirror(t, ctx, pool, 11, "blank", "Binance", "", "", false, seen)
	insertScope(t, ctx, pool, 11, 13, since)

	rows := unmatched(t, h, authz, "")
	one := findUnmatched(t, rows, "binance", "uid-1")
	if one.Reason != "no_exchange_account" || !one.Traded || one.ScopeCount != 1 || len(one.ApiIds) != 2 || len(one.MissingKinds) != 0 {
		t.Fatalf("uid-1 %+v", one)
	}
	if one.ApiIds[0].APIID != 1 || one.ApiIds[1].APIID != 2 || !one.Since.Equal(since) || !one.FirstSeen.Equal(earlier) {
		t.Fatalf("uid-1 ids %+v since %s seen %s", one.ApiIds, one.Since, one.FirstSeen)
	}
	if n := identityCount(rows); n != 4 { // uid-1, uid-mix, uid-dead, blank
		t.Fatalf("identities %d %+v", n, reasons(rows))
	}
	if row := findUnmatched(t, rows, "binance", "uid-pm"); row.Reason != "kind_out_of_scope" || !row.Traded || row.ApiIds[0].Kind != "BinancePM" {
		t.Fatalf("pm %+v", row)
	}
	if row := findUnmatched(t, rows, "binance", "uid-eo"); row.Reason != "kind_out_of_scope" || row.Traded {
		t.Fatalf("eo %+v", row)
	}
	if row := findUnmatched(t, rows, "binance", "uid-dead"); row.Reason != "no_exchange_account" || !row.Traded || row.ApiIds[0].DeletedAt == nil {
		t.Fatalf("dead %+v", row)
	}
	if row := findIdentity(rows, "missing_identity"); row == nil || !row.Traded || row.exchange() != "" || len(row.ApiIds) != 1 {
		t.Fatalf("blank %+v", row)
	}
	if findOptional(rows, "binance", "uid-gone") != nil {
		t.Fatal("deleted without scope is listed")
	}

	acc := decodeAccount(t, call(h, http.MethodPost, "/exchange-accounts", `{"exchange":"binance","exch_account_uid":"uid-1","label":"desk","kinds":["BinanceUM"]}`, authz))
	rows = unmatched(t, h, authz, "")
	one = findUnmatched(t, rows, "binance", "uid-1")
	if one.Reason != "kind_not_enabled" || len(one.MissingKinds) != 1 || one.MissingKinds[0] != "Binance" || !one.Traded || len(one.ApiIds) != 2 {
		t.Fatalf("after UM %+v", one)
	}
	if listed := accountAPIs(t, h, authz, acc.ID); len(listed) != 2 || listed[0].APIID != 1 || listed[1].APIID != 2 {
		t.Fatalf("joined %+v", listed)
	}

	enabled := decodeAccount(t, call(h, http.MethodPost, "/exchange-accounts/"+strconv.FormatInt(acc.ID, 10)+"/kinds", `{"kind":"Binance"}`, authz))
	if findOptional(unmatched(t, h, authz, ""), "binance", "uid-1") != nil {
		t.Fatal("covered identity is still unmatched")
	}
	if len(enabled.Kinds) != 2 {
		t.Fatalf("kinds %+v", enabled.Kinds)
	}

	mix := decodeAccount(t, call(h, http.MethodPost, "/exchange-accounts", `{"exchange":"binance","exch_account_uid":"uid-mix","label":"mix","kinds":["BinanceUM"]}`, authz))
	rows = unmatched(t, h, authz, "")
	if findOptional(rows, "binance", "uid-mix") != nil && findUnmatched(t, rows, "binance", "uid-mix").Reason != "kind_out_of_scope" {
		t.Fatal("mix became kind_not_enabled")
	}
	var mixKinds []string
	for _, row := range rows {
		if row.exchange() == "binance" && row.uid() == "uid-mix" {
			if row.Reason != "kind_out_of_scope" {
				t.Fatalf("mix %+v", row)
			}
			mixKinds = append(mixKinds, row.ApiIds[0].Kind)
		}
	}
	if len(mixKinds) != 2 {
		t.Fatalf("mix kinds %v", mixKinds)
	}
	_ = mix

	only := decodeAccount(t, call(h, http.MethodPost, "/exchange-accounts", `{"exchange":"bybit","exch_account_uid":"only-cpanel","label":"only"}`, authz))
	if findOptional(unmatched(t, h, authz, ""), "bybit", "only-cpanel") != nil {
		t.Fatal("cpanel-only account is unmatched")
	}
	if len(accountAPIs(t, h, authz, only.ID)) != 0 {
		t.Fatal("cpanel-only account has api ids")
	}

	oos := unmatched(t, h, authz, "kind_out_of_scope")
	for _, row := range oos {
		if row.Reason != "kind_out_of_scope" {
			t.Fatalf("filter %+v", row)
		}
	}
	if len(oos) < 4 { // PM, EO, CM, mix EO, Nope
		t.Fatalf("out of scope %d", len(oos))
	}

	call(h, http.MethodPost, "/exchange-accounts/"+strconv.FormatInt(acc.ID, 10)+"/kinds/Binance/disable", "", authz)
	one = findUnmatched(t, unmatched(t, h, authz, ""), "binance", "uid-1")
	if one.Reason != "kind_not_enabled" || len(one.MissingKinds) != 1 || one.MissingKinds[0] != "Binance" {
		t.Fatalf("after disable %+v", one)
	}
	if call(h, http.MethodDelete, "/exchange-accounts/"+strconv.FormatInt(acc.ID, 10), "", authz).Code != http.StatusOK {
		t.Fatal("delete")
	}
	one = findUnmatched(t, unmatched(t, h, authz, ""), "binance", "uid-1")
	if one.Reason != "no_exchange_account" || len(one.ApiIds) != 2 {
		t.Fatalf("after delete %+v", one)
	}
}

type unmatchedRow struct {
	Exchange     *string    `json:"exchange"`
	UID          *string    `json:"exch_account_uid"`
	ApiIds       []mirrored `json:"api_ids"`
	MissingKinds []string   `json:"missing_kinds"`
	Traded       bool       `json:"traded"`
	ScopeCount   int64      `json:"scope_count"`
	Since        *time.Time `json:"since"`
	Reason       string     `json:"reason"`
	FirstSeen    time.Time  `json:"first_seen_at"`
}

type mirrored struct {
	APIID     int64      `json:"api_id"`
	Name      string     `json:"name"`
	Kind      string     `json:"kind"`
	DeletedAt *time.Time `json:"deleted_at"`
}

func (r unmatchedRow) exchange() string {
	if r.Exchange == nil {
		return ""
	}
	return *r.Exchange
}

func (r unmatchedRow) uid() string {
	if r.UID == nil {
		return ""
	}
	return *r.UID
}

func unmatched(t *testing.T, h http.Handler, header http.Header, reason string) []unmatchedRow {
	t.Helper()
	path := "/exchange-accounts/unmatched"
	if reason != "" {
		path += "?reason=" + reason
	}
	rec := call(h, http.MethodGet, path, "", header)
	if rec.Code != http.StatusOK {
		t.Fatalf("unmatched %d %s", rec.Code, rec.Body.String())
	}
	var rows []unmatchedRow
	if err := json.Unmarshal(rec.Body.Bytes(), &rows); err != nil {
		t.Fatal(err)
	}
	return rows
}

func findUnmatched(t *testing.T, rows []unmatchedRow, exchange, uid string) unmatchedRow {
	t.Helper()
	row := findOptional(rows, exchange, uid)
	if row == nil {
		t.Fatalf("missing %s %s in %d rows", exchange, uid, len(rows))
	}
	return *row
}

func findOptional(rows []unmatchedRow, exchange, uid string) *unmatchedRow {
	for i := range rows {
		if rows[i].exchange() == exchange && rows[i].uid() == uid && rows[i].Reason != "kind_out_of_scope" {
			return &rows[i]
		}
	}
	for i := range rows {
		if rows[i].exchange() == exchange && rows[i].uid() == uid {
			return &rows[i]
		}
	}
	return nil
}

func findIdentity(rows []unmatchedRow, reason string) *unmatchedRow {
	for i := range rows {
		if rows[i].Reason == reason {
			return &rows[i]
		}
	}
	return nil
}

func identityCount(rows []unmatchedRow) int {
	n := 0
	for _, row := range rows {
		if row.Reason != "kind_out_of_scope" {
			n++
		}
	}
	return n
}

func reasons(rows []unmatchedRow) []string {
	out := make([]string, len(rows))
	for i, row := range rows {
		out[i] = row.exchange() + "/" + row.uid() + "/" + row.Reason
	}
	return out
}

func accountAPIs(t *testing.T, h http.Handler, header http.Header, id int64) []mirrored {
	t.Helper()
	rec := call(h, http.MethodGet, "/exchange-accounts/"+strconv.FormatInt(id, 10), "", header)
	if rec.Code != http.StatusOK {
		t.Fatalf("get %d %s", rec.Code, rec.Body.String())
	}
	var body struct {
		APIs []mirrored `json:"api_ids"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	return body.APIs
}

func insertMirror(t *testing.T, ctx context.Context, pool *pgxpool.Pool, id int64, name, kind, exchange, uid string, deleted bool, seen time.Time) {
	t.Helper()
	var exch, accountUID *string
	if exchange != "" {
		exch = &exchange
	}
	if uid != "" {
		accountUID = &uid
	}
	var deletedAt *time.Time
	if deleted {
		at := seen
		deletedAt = &at
	}
	if _, err := pool.Exec(ctx, `
		INSERT INTO mirror.account (api_id, name, kind, exchange, exch_account_uid, account_revision, deleted_at, first_seen_at, last_seen_at)
		VALUES ($1, $2, $3, $4, $5, 1, $6, $7, $7)`,
		id, name, kind, exch, accountUID, deletedAt, seen); err != nil {
		t.Fatal(err)
	}
}

func insertScope(t *testing.T, ctx context.Context, pool *pgxpool.Pool, apiID, uid int64, since time.Time) {
	t.Helper()
	if _, err := pool.Exec(ctx, `
		INSERT INTO mirror.scope (api_id, uid, since, first_seen_at, last_seen_at)
		VALUES ($1, $2, $3, $3, $3)`, apiID, uid, since); err != nil {
		t.Fatal(err)
	}
}
