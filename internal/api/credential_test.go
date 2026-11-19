package api

import (
	"context"
	"encoding/json"
	"net/http"
	"strconv"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/rs/zerolog"

	"github.com/BullionBear/cpanel/internal/auth"
	"github.com/BullionBear/cpanel/internal/platform/pgtest"
	"github.com/BullionBear/cpanel/internal/platform/sealbox/open"
	"github.com/BullionBear/cpanel/internal/reconcile"
	"github.com/BullionBear/cpanel/migrations"
)

func TestRegisterCredential(t *testing.T) {
	db := pgtest.Start(t)
	ctx := context.Background()
	if _, err := migrations.Up(ctx, db.DSN()); err != nil {
		t.Fatal(err)
	}
	pool := poolFor(t, db.DSN())
	key, err := open.GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	h := Handler(Config{Log: zerolog.Nop(), Pool: pool, Auth: testAuthSettings(), Sealbox: key.Public(), HasSealbox: true})
	raw := "cpanel_credential_test"
	if _, err := (&auth.Store{Pool: pool}).CreateToken(ctx, "88", "ada", "credentials", raw); err != nil {
		t.Fatal(err)
	}
	authz := bearerHeader(raw)
	secret := "super-secret-value"
	phrase := "desk-note"

	problemOf(t, call(h, http.MethodPost, "/exchange-accounts/1/credentials", `{"api_key":"abcd","api_secret":"x"}`, nil), http.StatusUnauthorized, "unauthenticated")

	binance := decodeAccount(t, call(h, http.MethodPost, "/exchange-accounts", `{"exchange":"binance","exch_account_uid":"uid-88","label":"desk","kinds":["Binance","BinanceUM"]}`, authz))
	body := `{"api_key":"  key-1234  ","api_secret":"` + secret + `"}`
	rec := call(h, http.MethodPost, "/exchange-accounts/"+strconv.FormatInt(binance.ID, 10)+"/credentials", body, authz)
	if rec.Code != http.StatusAccepted {
		t.Fatalf("register %d %s", rec.Code, rec.Body.String())
	}
	if strings.Contains(rec.Body.String(), secret) {
		t.Fatal("response contains the secret")
	}
	var got struct {
		ID         int64   `json:"id"`
		Status     string  `json:"status"`
		Last4      string  `json:"api_key_last4"`
		Reason     *string `json:"failure_reason"`
		VerifiedAt *string `json:"verified_at"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &got); err != nil {
		t.Fatal(err)
	}
	if got.Status != "unverified" || got.Last4 != "1234" || got.Reason != nil || got.VerifiedAt != nil {
		t.Fatalf("%+v", got)
	}
	var sealed []byte
	if err := pool.QueryRow(ctx, `SELECT ciphertext FROM pms.credential WHERE id = $1`, got.ID).Scan(&sealed); err != nil {
		t.Fatal(err)
	}
	opened, err := open.OpenCredentials(key, sealed, binance.ID, got.ID)
	if err != nil {
		t.Fatal(err)
	}
	if opened.APIKey != "key-1234" || opened.APISecret != secret || opened.Passphrase != "" {
		t.Fatal("opened credentials do not match")
	}
	var kind, dedup, lease string
	var credID int64
	if err := pool.QueryRow(ctx, `
		SELECT kind, dedup_key, lease_key, (params->>'credential_id')::bigint
		FROM ingest.job WHERE dedup_key = $1`, reconcile.CredentialDedup(got.ID)).Scan(&kind, &dedup, &lease, &credID); err != nil {
		t.Fatal(err)
	}
	if kind != reconcile.CredentialVerifyKind || lease != reconcile.AccountLease(binance.ID) || credID != got.ID {
		t.Fatalf("job %s %s %s %d", kind, dedup, lease, credID)
	}
	var revision int64
	if err := pool.QueryRow(ctx, `SELECT revision FROM pms.exchange_account WHERE id = $1`, binance.ID).Scan(&revision); err != nil {
		t.Fatal(err)
	}
	if revision != 2 {
		t.Fatalf("revision %d", revision)
	}
	var params string
	if err := pool.QueryRow(ctx, `SELECT params::text FROM meta.audit WHERE action = $1`, credentialCreate).Scan(&params); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(params, secret) || strings.Contains(params, "key-1234") {
		t.Fatal("audit contains a secret")
	}
	listed := call(h, http.MethodGet, "/exchange-accounts/"+strconv.FormatInt(binance.ID, 10), "", authz).Body.String()
	if !strings.Contains(listed, `"api_key_last4":"1234"`) || strings.Contains(listed, secret) {
		t.Fatal("account summary is missing the last 4 or contains the secret")
	}

	problemOf(t, call(h, http.MethodPost, "/exchange-accounts/"+strconv.FormatInt(binance.ID, 10)+"/credentials", body, authz), http.StatusConflict, "conflict")
	problemOf(t, call(h, http.MethodPost, "/exchange-accounts/"+strconv.FormatInt(binance.ID, 10)+"/credentials", `{"api_key":"abcd","api_secret":"x","passphrase":"no"}`, authz), http.StatusBadRequest, "bad_request")

	okx := decodeAccount(t, call(h, http.MethodPost, "/exchange-accounts", `{"exchange":"okx","exch_account_uid":"uid-okx","label":"okx"}`, authz))
	problemOf(t, call(h, http.MethodPost, "/exchange-accounts/"+strconv.FormatInt(okx.ID, 10)+"/credentials", `{"api_key":"okx-key","api_secret":"`+secret+`"}`, authz), http.StatusBadRequest, "bad_request")
	okxBody, err := json.Marshal(map[string]string{"api_key": "okx-key", "api_secret": secret, "passphrase": phrase})
	if err != nil {
		t.Fatal(err)
	}
	okxRec := call(h, http.MethodPost, "/exchange-accounts/"+strconv.FormatInt(okx.ID, 10)+"/credentials", string(okxBody), authz)
	if okxRec.Code != http.StatusAccepted || strings.Contains(okxRec.Body.String(), secret) || strings.Contains(okxRec.Body.String(), phrase) {
		t.Fatalf("okx %d", okxRec.Code)
	}
	var okxID int64
	if err := json.Unmarshal(okxRec.Body.Bytes(), &struct {
		ID *int64 `json:"id"`
	}{ID: &okxID}); err != nil {
		t.Fatal(err)
	}
	var okxSealed []byte
	if err := pool.QueryRow(ctx, `SELECT ciphertext FROM pms.credential WHERE id = $1`, okxID).Scan(&okxSealed); err != nil {
		t.Fatal(err)
	}
	opened, err = open.OpenCredentials(key, okxSealed, okx.ID, okxID)
	if err != nil || opened.Passphrase != phrase {
		t.Fatal("okx passphrase did not round-trip")
	}

	if call(h, http.MethodDelete, "/exchange-accounts/"+strconv.FormatInt(binance.ID, 10), "", authz).Code != http.StatusOK {
		t.Fatal("delete")
	}
	problemOf(t, call(h, http.MethodPost, "/exchange-accounts/"+strconv.FormatInt(binance.ID, 10)+"/credentials", body, authz), http.StatusNotFound, "not_found")
	problemOf(t, call(h, http.MethodPost, "/exchange-accounts/999999/credentials", body, authz), http.StatusNotFound, "not_found")

	bare := Handler(Config{Log: zerolog.Nop(), Pool: pool, Auth: testAuthSettings()})
	bybit := decodeAccount(t, call(bare, http.MethodPost, "/exchange-accounts", `{"exchange":"bybit","exch_account_uid":"uid-bybit","label":"bybit"}`, authz))
	problemOf(t, call(bare, http.MethodPost, "/exchange-accounts/"+strconv.FormatInt(bybit.ID, 10)+"/credentials", body, authz), http.StatusServiceUnavailable, "unavailable")
	if n := credentialCount(t, ctx, pool, bybit.ID); n != 0 {
		t.Fatalf("rows without a key %d", n)
	}
}

func TestCredentialLifecycle(t *testing.T) {
	db := pgtest.Start(t)
	ctx := context.Background()
	if _, err := migrations.Up(ctx, db.DSN()); err != nil {
		t.Fatal(err)
	}
	pool := poolFor(t, db.DSN())
	key, err := open.GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	h := Handler(Config{Log: zerolog.Nop(), Pool: pool, Auth: testAuthSettings(), Sealbox: key.Public(), HasSealbox: true})
	raw := "cpanel_credential_lifecycle"
	if _, err := (&auth.Store{Pool: pool}).CreateToken(ctx, "89", "ada", "lifecycle", raw); err != nil {
		t.Fatal(err)
	}
	authz := bearerHeader(raw)
	account := decodeAccount(t, call(h, http.MethodPost, "/exchange-accounts", `{"exchange":"binance","exch_account_uid":"uid-89","label":"desk","kinds":["Binance"]}`, authz))
	rec := call(h, http.MethodPost, "/exchange-accounts/"+strconv.FormatInt(account.ID, 10)+"/credentials", `{"api_key":"key-1234","api_secret":"secret"}`, authz)
	if rec.Code != http.StatusAccepted {
		t.Fatalf("register %d %s", rec.Code, rec.Body.String())
	}
	var cred struct {
		ID    int64  `json:"id"`
		Last4 string `json:"api_key_last4"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &cred); err != nil {
		t.Fatal(err)
	}
	base := "/exchange-accounts/" + strconv.FormatInt(account.ID, 10) + "/credentials/" + strconv.FormatInt(cred.ID, 10)

	problemOf(t, call(h, http.MethodPost, base+"/disable", "", nil), http.StatusUnauthorized, "unauthenticated")
	disabled := call(h, http.MethodPost, base+"/disable", "", authz)
	if disabled.Code != http.StatusOK || !strings.Contains(disabled.Body.String(), `"status":"disabled"`) {
		t.Fatalf("disable %d %s", disabled.Code, disabled.Body.String())
	}
	if jobs(t, ctx, pool, cred.ID) != 0 || revisionOf(t, ctx, pool, account.ID) != 3 || ciphertextLen(t, ctx, pool, cred.ID) == 0 {
		t.Fatalf("jobs %d revision %d length %d", jobs(t, ctx, pool, cred.ID), revisionOf(t, ctx, pool, account.ID), ciphertextLen(t, ctx, pool, cred.ID))
	}
	again := call(h, http.MethodPost, base+"/disable", "", authz)
	if again.Code != http.StatusOK || revisionOf(t, ctx, pool, account.ID) != 3 {
		t.Fatalf("second disable %d revision %d", again.Code, revisionOf(t, ctx, pool, account.ID))
	}
	problemOf(t, call(h, http.MethodPost, base+"/verify", "", authz), http.StatusConflict, "conflict")

	if _, err := pool.Exec(ctx, `UPDATE pms.credential SET status = 'rejected', failure_reason = 'not read-only' WHERE id = $1`, cred.ID); err != nil {
		t.Fatal(err)
	}
	verified := call(h, http.MethodPost, base+"/verify", "", authz)
	if verified.Code != http.StatusAccepted || !strings.Contains(verified.Body.String(), `"status":"unverified"`) {
		t.Fatalf("verify %d %s", verified.Code, verified.Body.String())
	}
	if jobs(t, ctx, pool, cred.ID) != 1 || revisionOf(t, ctx, pool, account.ID) != 4 {
		t.Fatalf("after verify jobs %d revision %d", jobs(t, ctx, pool, cred.ID), revisionOf(t, ctx, pool, account.ID))
	}
	if _, err := pool.Exec(ctx, `DELETE FROM ingest.job WHERE dedup_key = ANY($1)`, []string{reconcile.CredentialDedup(cred.ID), reconcile.CredentialProbeDedup(cred.ID)}); err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx, `
		UPDATE pms.credential SET status = 'auth_failed', next_probe_at = now()
		WHERE id = $1`, cred.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx, `
		INSERT INTO ingest.job (kind, dedup_key, lease_key, params)
		VALUES ($1, $2, $3, jsonb_build_object('credential_id', $4::bigint))`,
		reconcile.CredentialProbeKind, reconcile.CredentialProbeDedup(cred.ID), reconcile.AccountLease(account.ID), cred.ID); err != nil {
		t.Fatal(err)
	}
	againVerified := call(h, http.MethodPost, base+"/verify", "", authz)
	if againVerified.Code != http.StatusAccepted {
		t.Fatalf("verify from auth_failed %d %s", againVerified.Code, againVerified.Body.String())
	}
	var kind string
	if err := pool.QueryRow(ctx, `SELECT kind FROM ingest.job WHERE dedup_key = $1`, reconcile.CredentialDedup(cred.ID)).Scan(&kind); err != nil {
		t.Fatal(err)
	}
	if kind != reconcile.CredentialVerifyKind || jobs(t, ctx, pool, cred.ID) != 1 || revisionOf(t, ctx, pool, account.ID) != 5 {
		t.Fatalf("kind %s jobs %d revision %d", kind, jobs(t, ctx, pool, cred.ID), revisionOf(t, ctx, pool, account.ID))
	}

	deleted := call(h, http.MethodDelete, base, "", authz)
	if deleted.Code != http.StatusOK || !strings.Contains(deleted.Body.String(), `"api_key_last4":"1234"`) || strings.Contains(deleted.Body.String(), "secret") {
		t.Fatalf("delete %d %s", deleted.Code, deleted.Body.String())
	}
	if ciphertextLen(t, ctx, pool, cred.ID) != 0 || revisionOf(t, ctx, pool, account.ID) != 6 {
		t.Fatalf("length %d revision %d", ciphertextLen(t, ctx, pool, cred.ID), revisionOf(t, ctx, pool, account.ID))
	}
	var sealed []byte
	if err := pool.QueryRow(ctx, `SELECT ciphertext FROM pms.credential WHERE id = $1`, cred.ID).Scan(&sealed); err != nil {
		t.Fatal(err)
	}
	if _, err := open.OpenCredentials(key, sealed, account.ID, cred.ID); err == nil {
		t.Fatal("deleted ciphertext opened")
	}
	if call(h, http.MethodDelete, base, "", authz).Code != http.StatusOK || revisionOf(t, ctx, pool, account.ID) != 6 {
		t.Fatal("second delete changed the revision")
	}
	problemOf(t, call(h, http.MethodPost, "/exchange-accounts/"+strconv.FormatInt(account.ID, 10)+"/credentials/999/disable", "", authz), http.StatusNotFound, "not_found")

	other := decodeAccount(t, call(h, http.MethodPost, "/exchange-accounts", `{"exchange":"bybit","exch_account_uid":"uid-bybit-89","label":"bb"}`, authz))
	otherRec := call(h, http.MethodPost, "/exchange-accounts/"+strconv.FormatInt(other.ID, 10)+"/credentials", `{"api_key":"bybit-key","api_secret":"secret"}`, authz)
	if otherRec.Code != http.StatusAccepted {
		t.Fatalf("bybit register %d", otherRec.Code)
	}
	var otherCred struct {
		ID int64 `json:"id"`
	}
	if err := json.Unmarshal(otherRec.Body.Bytes(), &otherCred); err != nil {
		t.Fatal(err)
	}
	before := revisionOf(t, ctx, pool, other.ID)
	if call(h, http.MethodDelete, "/exchange-accounts/"+strconv.FormatInt(other.ID, 10), "", authz).Code != http.StatusOK {
		t.Fatal("delete account")
	}
	if revisionOf(t, ctx, pool, other.ID) != before || ciphertextLen(t, ctx, pool, otherCred.ID) != 0 {
		t.Fatalf("account delete revision %d length %d", revisionOf(t, ctx, pool, other.ID), ciphertextLen(t, ctx, pool, otherCred.ID))
	}
	if n := kindRows(t, ctx, pool, other.ID, "Bybit"); n != 1 {
		t.Fatalf("kind rows %d", n)
	}
}

func jobs(t *testing.T, ctx context.Context, pool *pgxpool.Pool, credentialID int64) int {
	t.Helper()
	var n int
	err := pool.QueryRow(ctx, `SELECT count(*) FROM ingest.job WHERE dedup_key = ANY($1)`,
		[]string{reconcile.CredentialDedup(credentialID), reconcile.CredentialProbeDedup(credentialID)}).Scan(&n)
	if err != nil {
		t.Fatal(err)
	}
	return n
}

func revisionOf(t *testing.T, ctx context.Context, pool *pgxpool.Pool, accountID int64) int64 {
	t.Helper()
	var n int64
	if err := pool.QueryRow(ctx, `SELECT revision FROM pms.exchange_account WHERE id = $1`, accountID).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

func ciphertextLen(t *testing.T, ctx context.Context, pool *pgxpool.Pool, credentialID int64) int {
	t.Helper()
	var n int
	if err := pool.QueryRow(ctx, `SELECT octet_length(ciphertext) FROM pms.credential WHERE id = $1`, credentialID).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

func credentialCount(t *testing.T, ctx context.Context, pool *pgxpool.Pool, accountID int64) int {
	t.Helper()
	var n int
	if err := pool.QueryRow(ctx, `SELECT count(*) FROM pms.credential WHERE exchange_account_id = $1`, accountID).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}
