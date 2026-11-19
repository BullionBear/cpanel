package credential

import (
	"bytes"
	"context"
	"encoding/hex"
	"errors"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BullionBear/cpanel/internal/platform/sealbox"
	"github.com/BullionBear/cpanel/internal/platform/sealbox/open"
	"github.com/BullionBear/cpanel/internal/venue"
	"github.com/BullionBear/cpanel/internal/worker"
)

func TestRekeyResumesWithoutRewriting(t *testing.T) {
	ctx, pool := testDB(t)
	oldKey := mustPrivate(t)
	newKey := mustPrivate(t)
	a := insertAccount(t, ctx, pool, "binance", "uid-a", "a")
	b := insertAccount(t, ctx, pool, "bybit", "uid-b", "b")
	c := insertAccount(t, ctx, pool, "okx", "uid-c", "c")

	first := insertCredential(t, ctx, pool, oldKey, a, "active", "key-a", "secret-a", "")
	second := insertCredential(t, ctx, pool, oldKey, a, "disabled", "key-b", "secret-b", "phrase-b")
	third := insertCredential(t, ctx, pool, oldKey, b, "active", "key-c", "secret-c", "")
	empty := insertEmptyCredential(t, ctx, pool, b)
	kept := insertCredential(t, ctx, pool, newKey, c, "active", "key-d", "secret-d", "phrase-d")
	keptBytes := ciphertextOf(t, ctx, pool, kept)
	thirdBytes := ciphertextOf(t, ctx, pool, third)

	k := Rekey{DB: pool, Current: newKey, Previous: oldKey, stopAfter: 2}
	res, err := k.Handle(ctx, &worker.Job{})
	if err != nil || res.Items != 2 || !res.More {
		t.Fatalf("stopped run: %+v %v", res, err)
	}
	if audits(t, ctx, pool, "sealbox.rekey") != 0 {
		t.Fatal("audit written before the rotation finished")
	}
	if rev(t, ctx, pool, a) != 1 || rev(t, ctx, pool, b) != 1 || rev(t, ctx, pool, c) != 1 {
		t.Fatal("rekey moved an account revision")
	}
	assertOpen(t, newKey, ciphertextOf(t, ctx, pool, first), a, first, "secret-a", "")
	assertOpen(t, newKey, ciphertextOf(t, ctx, pool, second), a, second, "secret-b", "phrase-b")
	assertOtherKey(t, newKey, thirdBytes, b, third)
	assertOpen(t, oldKey, thirdBytes, b, third, "secret-c", "")
	if !bytes.Equal(ciphertextOf(t, ctx, pool, third), thirdBytes) {
		t.Fatal("the row past the cut was rewritten")
	}
	if !bytes.Equal(ciphertextOf(t, ctx, pool, kept), keptBytes) {
		t.Fatal("a credential already on the new key was rewritten")
	}
	if len(ciphertextOf(t, ctx, pool, empty)) != 0 {
		t.Fatal("empty ciphertext was filled in")
	}
	if status(t, ctx, pool, first) != "active" || status(t, ctx, pool, second) != "disabled" {
		t.Fatal("rekey changed a credential status")
	}

	k.stopAfter = 0
	res, err = k.Handle(ctx, &worker.Job{})
	if err != nil || res.Items != 1 || res.More {
		t.Fatalf("resumed run: %+v %v", res, err)
	}
	for _, row := range []struct {
		id, account    int64
		secret, phrase string
	}{
		{first, a, "secret-a", ""},
		{second, a, "secret-b", "phrase-b"},
		{third, b, "secret-c", ""},
		{kept, c, "secret-d", "phrase-d"},
	} {
		box := ciphertextOf(t, ctx, pool, row.id)
		assertOpen(t, newKey, box, row.account, row.id, row.secret, row.phrase)
		assertOtherKey(t, oldKey, box, row.account, row.id)
	}
	if !bytes.Equal(ciphertextOf(t, ctx, pool, kept), keptBytes) {
		t.Fatal("resuming rewrote the credential that was already on the new key")
	}
	done := ciphertextOf(t, ctx, pool, first)
	res, err = k.Handle(ctx, &worker.Job{})
	if err != nil || res.Items != 0 || !bytes.Equal(ciphertextOf(t, ctx, pool, first), done) {
		t.Fatalf("second pass rewrote a finished row: %+v %v", res, err)
	}
	if audits(t, ctx, pool, "sealbox.rekey") != 1 {
		t.Fatal("rotation audit was not written once")
	}
	var params string
	if err := pool.QueryRow(ctx, `SELECT params::text FROM meta.audit WHERE action = 'sealbox.rekey'`).Scan(&params); err != nil {
		t.Fatal(err)
	}
	oldFP := oldKey.Public().Fingerprint()
	newFP := newKey.Public().Fingerprint()
	for _, want := range []string{`"resealed": 1`, hex.EncodeToString(oldFP[:]), hex.EncodeToString(newFP[:])} {
		if !strings.Contains(params, want) {
			t.Fatalf("audit params %s, missing %s", params, want)
		}
	}
	for _, secret := range []string{"secret-a", "secret-b", "secret-c", "phrase-b", "key-a"} {
		if strings.Contains(params, secret) || auditHas(t, ctx, pool, secret) {
			t.Fatalf("audit contains %s", secret)
		}
	}
}

func TestRekeyLeavesAMismatchedBox(t *testing.T) {
	ctx, pool := testDB(t)
	oldKey := mustPrivate(t)
	newKey := mustPrivate(t)
	account := insertAccount(t, ctx, pool, "binance", "uid-m", "m")
	id := insertCredential(t, ctx, pool, oldKey, account, "active", "key-m", "secret-m", "")
	forged, err := sealbox.SealCredentials(oldKey.Public(), account, id+99, sealbox.Credentials{APIKey: "key-m", APISecret: "secret-m"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx, `UPDATE pms.credential SET ciphertext = $2 WHERE id = $1`, id, forged); err != nil {
		t.Fatal(err)
	}
	_, err = (Rekey{DB: pool, Current: newKey, Previous: oldKey}).Handle(ctx, &worker.Job{})
	if err == nil || strings.Contains(err.Error(), "secret-m") {
		t.Fatalf("err %v", err)
	}
	if !bytes.Equal(ciphertextOf(t, ctx, pool, id), forged) {
		t.Fatal("mismatched ciphertext was written")
	}
	if audits(t, ctx, pool, "sealbox.rekey") != 0 || rev(t, ctx, pool, account) != 1 {
		t.Fatal("a failed row was audited or the revision moved")
	}
}

func TestCheckRecipientsCountsLeftovers(t *testing.T) {
	ctx, pool := testDB(t)
	oldKey := mustPrivate(t)
	newKey := mustPrivate(t)
	other := mustPrivate(t)
	account := insertAccount(t, ctx, pool, "okx", "uid-r", "r")
	insertCredential(t, ctx, pool, oldKey, account, "active", "key-r", "secret-r", "pp")
	insertEmptyCredential(t, ctx, pool, account)

	err := CheckRecipients(ctx, pool, newKey, nil)
	if err == nil || !strings.Contains(err.Error(), "1 credential") || strings.Contains(err.Error(), "secret-r") {
		t.Fatalf("only the new key: %v", err)
	}
	if err := CheckRecipients(ctx, pool, newKey, other); err == nil || !strings.Contains(err.Error(), "1 credential") {
		t.Fatalf("wrong previous key: %v", err)
	}
	if err := CheckRecipients(ctx, pool, newKey, oldKey); err != nil {
		t.Fatal(err)
	}
	if err := CheckRecipients(ctx, pool, oldKey, nil); err != nil {
		t.Fatalf("current key still matches: %v", err)
	}
	if _, err := (Rekey{DB: pool, Current: newKey}).Handle(ctx, &worker.Job{}); err == nil || !strings.Contains(err.Error(), "1 credential") {
		t.Fatalf("handler without the previous key: %v", err)
	}
}

func TestVerifyOpensPreviousKey(t *testing.T) {
	ctx, pool := testDB(t)
	oldKey := mustPrivate(t)
	newKey := mustPrivate(t)
	account := insertAccount(t, ctx, pool, "binance", "uid-v", "v")
	enableKind(t, ctx, pool, account, "Binance")
	id := insertCredential(t, ctx, pool, oldKey, account, "unverified", "key-v", "secret-v", "")
	v := Verify{DB: pool, Key: newKey, Previous: oldKey, Open: func(_ string, cred sealbox.Credentials) (Checker, error) {
		if cred.APISecret != "secret-v" {
			t.Fatalf("opened secret %q", cred.APISecret)
		}
		return &fakeChecker{check: venue.Check{UID: "uid-v", ReadOnly: true, Summary: "read"}}, nil
	}}
	if _, err := v.Handle(ctx, job(id)); err != nil {
		t.Fatal(err)
	}
	if status(t, ctx, pool, id) != "active" {
		t.Fatalf("status %s", status(t, ctx, pool, id))
	}
}

func mustPrivate(t *testing.T) *open.PrivateKey {
	t.Helper()
	k, err := open.GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	return k
}

func insertEmptyCredential(t *testing.T, ctx context.Context, pool *pgxpool.Pool, account int64) int64 {
	t.Helper()
	var id int64
	err := pool.QueryRow(ctx, `
		INSERT INTO pms.credential (exchange_account_id, purpose, status, ciphertext, api_key_last4)
		VALUES ($1, 'read', 'disabled', $2, '1234') RETURNING id`, account, []byte{}).Scan(&id)
	if err != nil {
		t.Fatal(err)
	}
	return id
}

func ciphertextOf(t *testing.T, ctx context.Context, pool *pgxpool.Pool, id int64) []byte {
	t.Helper()
	var b []byte
	if err := pool.QueryRow(ctx, `SELECT ciphertext FROM pms.credential WHERE id = $1`, id).Scan(&b); err != nil {
		t.Fatal(err)
	}
	return b
}

func audits(t *testing.T, ctx context.Context, pool *pgxpool.Pool, action string) int {
	t.Helper()
	var n int
	if err := pool.QueryRow(ctx, `SELECT count(*) FROM meta.audit WHERE action = $1`, action).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

func assertOpen(t *testing.T, key *open.PrivateKey, sealed []byte, account, id int64, secret, phrase string) {
	t.Helper()
	got, err := open.OpenCredentials(key, sealed, account, id)
	if err != nil || got.APISecret != secret || got.Passphrase != phrase {
		t.Fatalf("open account %d credential %d: %v", account, id, err)
	}
}

func assertOtherKey(t *testing.T, key *open.PrivateKey, sealed []byte, account, id int64) {
	t.Helper()
	_, err := open.OpenCredentials(key, sealed, account, id)
	if !errors.Is(err, sealbox.ErrOtherKey) {
		t.Fatalf("open with the other key: %v", err)
	}
}
