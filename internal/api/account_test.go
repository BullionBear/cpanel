package api

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/rs/zerolog"

	"github.com/BullionBear/cpanel/internal/auth"
	"github.com/BullionBear/cpanel/internal/platform/pgtest"
	"github.com/BullionBear/cpanel/migrations"
)

func TestExchangeAccounts(t *testing.T) {
	db := pgtest.Start(t)
	ctx := context.Background()
	if _, err := migrations.Up(ctx, db.DSN()); err != nil {
		t.Fatal(err)
	}
	pool := poolFor(t, db.DSN())
	h := Handler(Config{Log: zerolog.Nop(), Pool: pool, Auth: testAuthSettings()})
	raw := "cpanel_account_test"
	token, err := (&auth.Store{Pool: pool}).CreateToken(ctx, "42", "ada", "accounts", raw)
	if err != nil {
		t.Fatal(err)
	}
	authz := bearerHeader(raw)
	actor := "token:" + token.ID

	t.Run("unauthenticated", func(t *testing.T) {
		problemOf(t, call(h, http.MethodGet, "/exchange-accounts", "", nil), http.StatusUnauthorized, "unauthenticated")
		problemOf(t, call(h, http.MethodPost, "/exchange-accounts", `{"exchange":"binance","exch_account_uid":"x","label":"x"}`, nil), http.StatusUnauthorized, "unauthenticated")
	})

	t.Run("cookie without origin", func(t *testing.T) {
		if err := (&auth.Store{Pool: pool}).CreateSession(ctx, "session-raw", auth.User{ID: "7", Username: "bea", DisplayName: "Bea"}, time.Hour); err != nil {
			t.Fatal(err)
		}
		rec := call(h, http.MethodPost, "/exchange-accounts", `{"exchange":"binance","exch_account_uid":"csrf","label":"csrf"}`, cookieHeader("session-raw"))
		problemOf(t, rec, http.StatusForbidden, "forbidden")
		if n := accountsNamed(t, ctx, pool, "binance", "csrf"); n != 0 {
			t.Fatalf("rows %d", n)
		}
	})

	var binanceID int64
	t.Run("binance", func(t *testing.T) {
		rec := call(h, http.MethodPost, "/exchange-accounts", `{
			"exchange":"binance","exch_account_uid":"  binance-1  ","label":"  desk  ",
			"kinds":["Binance","BinanceUM","Binance"]
		}`, authz)
		if rec.Code != http.StatusCreated {
			t.Fatalf("create %d %s", rec.Code, rec.Body.String())
		}
		acc := decodeAccount(t, rec)
		binanceID = acc.ID
		if acc.Exchange != "binance" || acc.ExchAccountUid != "binance-1" || acc.Label != "desk" || acc.Revision != 1 || acc.DeletedAt != nil {
			t.Fatalf("%+v", acc)
		}
		if len(acc.Kinds) != 2 || acc.Kinds[0].Kind != "Binance" || acc.Kinds[1].Kind != "BinanceUM" {
			t.Fatalf("kinds %+v", acc.Kinds)
		}
		if acc.Kinds[0].DisabledAt != nil || acc.Kinds[0].VerifiedAt != nil || acc.Kinds[0].LastError != nil {
			t.Fatalf("verify fields %+v", acc.Kinds[0])
		}
		if n := auditCount(t, ctx, pool, accountCreate, accountTarget(binanceID)); n != 1 {
			t.Fatalf("create audit %d", n)
		}
		if actorOf(t, ctx, pool, accountCreate, accountTarget(binanceID)) != actor {
			t.Fatalf("actor %s", actorOf(t, ctx, pool, accountCreate, accountTarget(binanceID)))
		}

		bare := decodeAccount(t, call(h, http.MethodPost, "/exchange-accounts", `{"exchange":"binance","exch_account_uid":"binance-empty","label":"empty"}`, authz))
		if bare.Revision != 1 || len(bare.Kinds) != 0 || !strings.Contains(call(h, http.MethodGet, "/exchange-accounts/"+strconv.FormatInt(bare.ID, 10), "", authz).Body.String(), `"kinds":[]`) {
			got := call(h, http.MethodGet, "/exchange-accounts/"+strconv.FormatInt(bare.ID, 10), "", authz)
			t.Fatalf("empty kinds %+v %s", bare, got.Body.String())
		}

		if _, err := pool.Exec(ctx, `
			UPDATE pms.exchange_account_kind
			SET verified_at = now(), last_error = 'probe'
			WHERE exchange_account_id = $1 AND kind = 'Binance'`, binanceID); err != nil {
			t.Fatal(err)
		}
		disabled := decodeAccount(t, call(h, http.MethodPost, "/exchange-accounts/"+strconv.FormatInt(binanceID, 10)+"/kinds/Binance/disable", "", authz))
		if disabled.Revision != 2 {
			t.Fatalf("revision %d", disabled.Revision)
		}
		row := kindOf(t, disabled, "Binance")
		if row.DisabledAt == nil || row.VerifiedAt == nil || row.LastError == nil || *row.LastError != "probe" {
			t.Fatalf("after disable %+v", row)
		}
		if n := kindRows(t, ctx, pool, binanceID, "Binance"); n != 1 {
			t.Fatalf("kind rows %d", n)
		}

		again := decodeAccount(t, call(h, http.MethodPost, "/exchange-accounts/"+strconv.FormatInt(binanceID, 10)+"/kinds/Binance/disable", "", authz))
		if again.Revision != 2 || auditCount(t, ctx, pool, accountKindOff, accountTarget(binanceID)) != 1 {
			t.Fatalf("second disable rev %d audit %d", again.Revision, auditCount(t, ctx, pool, accountKindOff, accountTarget(binanceID)))
		}

		enabled := decodeAccount(t, call(h, http.MethodPost, "/exchange-accounts/"+strconv.FormatInt(binanceID, 10)+"/kinds", `{"kind":"Binance"}`, authz))
		if enabled.Revision != 3 {
			t.Fatalf("re-enable revision %d", enabled.Revision)
		}
		row = kindOf(t, enabled, "Binance")
		if row.DisabledAt != nil || row.VerifiedAt != nil || row.LastError != nil {
			t.Fatalf("after enable %+v", row)
		}
		same := decodeAccount(t, call(h, http.MethodPost, "/exchange-accounts/"+strconv.FormatInt(binanceID, 10)+"/kinds", `{"kind":"Binance"}`, authz))
		if same.Revision != 3 || auditCount(t, ctx, pool, accountKindEnable, accountTarget(binanceID)) != 1 {
			t.Fatalf("second enable rev %d audit %d", same.Revision, auditCount(t, ctx, pool, accountKindEnable, accountTarget(binanceID)))
		}

		renamed := decodeAccount(t, call(h, http.MethodPatch, "/exchange-accounts/"+strconv.FormatInt(binanceID, 10), `{"label":"window"}`, authz))
		if renamed.Label != "window" || renamed.Revision != 4 || auditCount(t, ctx, pool, accountLabel, accountTarget(binanceID)) != 1 {
			t.Fatalf("%+v", renamed)
		}
		unchanged := decodeAccount(t, call(h, http.MethodPatch, "/exchange-accounts/"+strconv.FormatInt(binanceID, 10), `{"label":"window","exch_account_uid":"binance-1"}`, authz))
		if unchanged.Revision != 4 {
			t.Fatalf("same label revision %d", unchanged.Revision)
		}
		problemOf(t, call(h, http.MethodPatch, "/exchange-accounts/"+strconv.FormatInt(binanceID, 10), `{"label":"nope","exch_account_uid":"other"}`, authz), http.StatusBadRequest, "bad_request")
		got := decodeAccount(t, call(h, http.MethodGet, "/exchange-accounts/"+strconv.FormatInt(binanceID, 10), "", authz))
		if got.Label != "window" || got.ExchAccountUid != "binance-1" || got.Revision != 4 {
			t.Fatalf("after rejected uid %+v", got)
		}
		if auditCount(t, ctx, pool, accountLabel, accountTarget(binanceID)) != 1 {
			t.Fatal("failed update wrote audit")
		}

		deleted := decodeAccount(t, call(h, http.MethodDelete, "/exchange-accounts/"+strconv.FormatInt(binanceID, 10), "", authz))
		if deleted.DeletedAt == nil || deleted.Revision != 4 {
			t.Fatalf("deleted %+v", deleted)
		}
		still := decodeAccount(t, call(h, http.MethodGet, "/exchange-accounts/"+strconv.FormatInt(binanceID, 10), "", authz))
		if still.DeletedAt == nil {
			t.Fatal("get after delete missed the row")
		}
		listed := getJSON[[]accountJSON](t, h, "/exchange-accounts", authz)
		for _, row := range listed {
			if row.ID == binanceID {
				t.Fatalf("deleted account is in the list: %+v", listed)
			}
		}
		second := decodeAccount(t, call(h, http.MethodDelete, "/exchange-accounts/"+strconv.FormatInt(binanceID, 10), "", authz))
		if second.Revision != 4 || auditCount(t, ctx, pool, accountDelete, accountTarget(binanceID)) != 1 {
			t.Fatalf("second delete rev %d", second.Revision)
		}
		problemOf(t, call(h, http.MethodPost, "/exchange-accounts", `{"exchange":"binance","exch_account_uid":"binance-1","label":"again"}`, authz), http.StatusConflict, "conflict")
		problemOf(t, call(h, http.MethodPatch, "/exchange-accounts/"+strconv.FormatInt(binanceID, 10), `{"label":"later"}`, authz), http.StatusConflict, "conflict")
		problemOf(t, call(h, http.MethodPost, "/exchange-accounts/"+strconv.FormatInt(binanceID, 10)+"/kinds", `{"kind":"Binance"}`, authz), http.StatusConflict, "conflict")
		if got.Revision != 4 {
			t.Fatal("revision moved")
		}
		fresh := decodeAccount(t, call(h, http.MethodGet, "/exchange-accounts/"+strconv.FormatInt(binanceID, 10), "", authz))
		if fresh.Label != "window" || fresh.Revision != 4 {
			t.Fatalf("after rejected writes %+v", fresh)
		}
	})

	t.Run("bybit and okx", func(t *testing.T) {
		bybit := decodeAccount(t, call(h, http.MethodPost, "/exchange-accounts", `{"exchange":"bybit","exch_account_uid":"bybit-1","label":"bb"}`, authz))
		if len(bybit.Kinds) != 1 || bybit.Kinds[0].Kind != "Bybit" || bybit.Kinds[0].DisabledAt != nil || bybit.Revision != 1 {
			t.Fatalf("%+v", bybit)
		}
		okx := decodeAccount(t, call(h, http.MethodPost, "/exchange-accounts", `{"exchange":"okx","exch_account_uid":"okx-1","label":"oo","kinds":[]}`, authz))
		if len(okx.Kinds) != 1 || okx.Kinds[0].Kind != "Okx" || okx.Kinds[0].DisabledAt != nil {
			t.Fatalf("%+v", okx)
		}
	})

	t.Run("rejected kinds", func(t *testing.T) {
		before := accountsNamed(t, ctx, pool, "binance", "bad-mix")
		problemOf(t, call(h, http.MethodPost, "/exchange-accounts", `{"exchange":"binance","exch_account_uid":"bad-mix","label":"x","kinds":["Binance","BinanceCM"]}`, authz), http.StatusBadRequest, "bad_request")
		if accountsNamed(t, ctx, pool, "binance", "bad-mix") != before {
			t.Fatal("failed create left a row")
		}
		host := decodeAccount(t, call(h, http.MethodPost, "/exchange-accounts", `{"exchange":"binance","exch_account_uid":"kind-host","label":"host"}`, authz))
		for _, kind := range []string{"Bybit", "BinanceCM", "BinancePM", "BinanceEO", "Nope"} {
			problemOf(t, call(h, http.MethodPost, "/exchange-accounts/"+strconv.FormatInt(host.ID, 10)+"/kinds", `{"kind":"`+kind+`"}`, authz), http.StatusBadRequest, "bad_request")
		}
		if kindRows(t, ctx, pool, host.ID, "") != 0 {
			t.Fatal("rejected enable left a kind row")
		}
		if decodeAccount(t, call(h, http.MethodGet, "/exchange-accounts/"+strconv.FormatInt(host.ID, 10), "", authz)).Revision != 1 {
			t.Fatal("failed enable bumped revision")
		}
		problemOf(t, call(h, http.MethodGet, "/exchange-accounts/999", "", authz), http.StatusNotFound, "not_found")
		problemOf(t, call(h, http.MethodPost, "/exchange-accounts/"+strconv.FormatInt(host.ID, 10)+"/kinds/Binance/disable", "", authz), http.StatusNotFound, "not_found")
	})

	t.Run("trigger", func(t *testing.T) {
		var id int64
		if err := pool.QueryRow(ctx, `
			INSERT INTO pms.exchange_account (exchange, exch_account_uid, label)
			VALUES ('binance', 'trigger-1', 'trigger') RETURNING id`).Scan(&id); err != nil {
			t.Fatal(err)
		}
		for _, kind := range []string{"Bybit", "BinanceCM"} {
			_, err := pool.Exec(ctx, `INSERT INTO pms.exchange_account_kind (exchange_account_id, kind) VALUES ($1, $2)`, id, kind)
			var pgErr *pgconn.PgError
			if !errors.As(err, &pgErr) || pgErr.Code != "23514" {
				t.Fatalf("%s: %v", kind, err)
			}
		}
		if kindRows(t, ctx, pool, id, "") != 0 {
			t.Fatal("trigger left a row")
		}
	})
}

type accountJSON struct {
	ID             int64      `json:"id"`
	Exchange       string     `json:"exchange"`
	ExchAccountUid string     `json:"exch_account_uid"`
	Label          string     `json:"label"`
	Revision       int64      `json:"revision"`
	DeletedAt      *time.Time `json:"deleted_at"`
	Kinds          []kindJSON `json:"kinds"`
}

type kindJSON struct {
	Kind       string     `json:"kind"`
	DisabledAt *time.Time `json:"disabled_at"`
	VerifiedAt *time.Time `json:"verified_at"`
	LastError  *string    `json:"last_error"`
}

func decodeAccount(t *testing.T, rec *httptest.ResponseRecorder) accountJSON {
	t.Helper()
	if rec.Code != http.StatusOK && rec.Code != http.StatusCreated {
		t.Fatalf("status %d %s", rec.Code, rec.Body.String())
	}
	var acc accountJSON
	if err := json.Unmarshal(rec.Body.Bytes(), &acc); err != nil {
		t.Fatalf("%v %s", err, rec.Body.String())
	}
	return acc
}

func kindOf(t *testing.T, acc accountJSON, kind string) kindJSON {
	t.Helper()
	for _, row := range acc.Kinds {
		if row.Kind == kind {
			return row
		}
	}
	t.Fatalf("no kind %s in %+v", kind, acc.Kinds)
	return kindJSON{}
}

func auditCount(t *testing.T, ctx context.Context, pool *pgxpool.Pool, action, target string) int {
	t.Helper()
	var n int
	if err := pool.QueryRow(ctx, `SELECT count(*) FROM meta.audit WHERE action = $1 AND target = $2`, action, target).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

func actorOf(t *testing.T, ctx context.Context, pool *pgxpool.Pool, action, target string) string {
	t.Helper()
	var actor string
	if err := pool.QueryRow(ctx, `SELECT actor FROM meta.audit WHERE action = $1 AND target = $2`, action, target).Scan(&actor); err != nil {
		t.Fatal(err)
	}
	return actor
}

func accountsNamed(t *testing.T, ctx context.Context, pool *pgxpool.Pool, exchange, uid string) int {
	t.Helper()
	var n int
	if err := pool.QueryRow(ctx, `SELECT count(*) FROM pms.exchange_account WHERE exchange = $1 AND exch_account_uid = $2`, exchange, uid).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

func kindRows(t *testing.T, ctx context.Context, pool *pgxpool.Pool, id int64, kind string) int {
	t.Helper()
	var n int
	var err error
	if kind == "" {
		err = pool.QueryRow(ctx, `SELECT count(*) FROM pms.exchange_account_kind WHERE exchange_account_id = $1`, id).Scan(&n)
	} else {
		err = pool.QueryRow(ctx, `SELECT count(*) FROM pms.exchange_account_kind WHERE exchange_account_id = $1 AND kind = $2`, id, kind).Scan(&n)
	}
	if err != nil {
		t.Fatal(err)
	}
	return n
}
