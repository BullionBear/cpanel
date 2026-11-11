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

func credentialCount(t *testing.T, ctx context.Context, pool *pgxpool.Pool, accountID int64) int {
	t.Helper()
	var n int
	if err := pool.QueryRow(ctx, `SELECT count(*) FROM pms.credential WHERE exchange_account_id = $1`, accountID).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}
