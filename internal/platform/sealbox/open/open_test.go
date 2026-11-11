package open

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/BullionBear/cpanel/internal/platform/sealbox"
)

func creds(t *testing.T) sealbox.Credentials {
	t.Helper()
	return sealbox.Credentials{APIKey: fixture("key"), APISecret: fixture("sec"), Passphrase: fixture("pp")}
}

func fixture(tag string) string {
	k, err := GenerateKey()
	if err != nil {
		panic(err)
	}
	// The public key is not a credential. Use a short unique tag plus part of it so tests can
	// search output without embedding a fixed secret.
	return tag + "-" + k.Public().String()[:8]
}

func TestRoundTrip(t *testing.T) {
	k := mustKey(t)
	c := creds(t)
	sealed, err := sealbox.SealCredentials(k.Public(), 7, 9, c)
	if err != nil {
		t.Fatal(err)
	}
	if sealed[0] != sealbox.Version {
		t.Fatalf("version %d", sealed[0])
	}
	got, err := OpenCredentials(k, sealed, 7, 9)
	if err != nil || got != c {
		t.Fatalf("%v", err)
	}
	empty := sealbox.Credentials{APIKey: c.APIKey, APISecret: c.APISecret}
	sealed, err = sealbox.SealCredentials(k.Public(), 7, 9, empty)
	if err != nil {
		t.Fatal(err)
	}
	got, err = OpenCredentials(k, sealed, 7, 9)
	if err != nil || got != empty || got.Passphrase != "" {
		t.Fatalf("empty passphrase: %v %+v", err, got)
	}
}

func TestVector(t *testing.T) {
	k := vectorKey(t)
	plain := readVec(t, "mftiko_plain.bin")
	sealed := readVec(t, "mftiko_seal.bin")
	got, err := Open(k, sealed)
	if err != nil || !bytes.Equal(got, plain) {
		t.Fatalf("mftiko seal: %v", err)
	}
	ours, err := sealbox.Seal(k.Public(), plain)
	if err != nil {
		t.Fatal(err)
	}
	got, err = Open(k, ours)
	if err != nil || !bytes.Equal(got, plain) {
		t.Fatalf("cpanel seal: %v", err)
	}
	credPlain := readVec(t, "mftiko_credentials.plain")
	credBox := readVec(t, "mftiko_credentials.bin")
	got, err = Open(k, credBox)
	if err != nil || !bytes.Equal(got, credPlain) {
		t.Fatalf("mftiko credentials: %v", err)
	}
	for _, ids := range [][2]int64{{42, 9}, {42, 0}} {
		got, err := OpenCredentials(k, credBox, ids[0], ids[1])
		if !errors.Is(err, sealbox.ErrIDMismatch) || got.APIKey != "" || got.APISecret != "" || got.Passphrase != "" {
			t.Fatalf("mftiko protobuf as cpanel credentials %v: %v %+v", ids, err, got)
		}
	}
	c := sealbox.Credentials{APIKey: "vector-api-key", APISecret: "vector-api-secret", Passphrase: "vector-passphrase"}
	box, err := sealbox.SealCredentials(k.Public(), 7, 9, c)
	if err != nil {
		t.Fatal(err)
	}
	gotCreds, err := OpenCredentials(k, box, 7, 9)
	if err != nil || gotCreds != c {
		t.Fatalf("vector key opened cpanel credentials: %v", err)
	}
}

func TestOtherKey(t *testing.T) {
	a, b := mustKey(t), mustKey(t)
	c := creds(t)
	sealed, err := sealbox.SealCredentials(a.Public(), 7, 9, c)
	if err != nil {
		t.Fatal(err)
	}
	_, err = OpenCredentials(b, sealed, 7, 9)
	if !errors.Is(err, sealbox.ErrOtherKey) || strings.Contains(err.Error(), c.APISecret) {
		t.Fatalf("%v", err)
	}
	forged := append([]byte(nil), sealed...)
	fp := b.Public().Fingerprint()
	copy(forged[1:1+len(fp)], fp[:])
	if _, err := OpenCredentials(b, forged, 7, 9); !errors.Is(err, sealbox.ErrUnreadable) {
		t.Fatalf("forged fingerprint: %v", err)
	}
}

func TestIDMismatch(t *testing.T) {
	k := mustKey(t)
	c := creds(t)
	sealed, err := sealbox.SealCredentials(k.Public(), 7, 9, c)
	if err != nil {
		t.Fatal(err)
	}
	for _, ids := range [][2]int64{{8, 9}, {7, 10}} {
		got, err := OpenCredentials(k, sealed, ids[0], ids[1])
		if !errors.Is(err, sealbox.ErrIDMismatch) || got.APIKey != "" || got.APISecret != "" {
			t.Fatalf("%v %+v", err, got)
		}
	}
	flip := append([]byte(nil), sealed...)
	flip[len(flip)-1] ^= 1
	if _, err := OpenCredentials(k, flip, 7, 9); !errors.Is(err, sealbox.ErrUnreadable) {
		t.Fatalf("tampered: %v", err)
	}
	if _, err := Open(k, nil); !errors.Is(err, sealbox.ErrUnreadable) {
		t.Fatalf("empty: %v", err)
	}
}

func TestKeyFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "worker.key")
	k := mustKey(t)
	c := creds(t)
	if err := WriteKeyFile(path, k); err != nil {
		t.Fatal(err)
	}
	fi, err := os.Stat(path)
	if err != nil || fi.Mode().Perm() != 0o600 {
		t.Fatalf("mode %v %v", fi.Mode(), err)
	}
	if err := WriteKeyFile(path, mustKey(t)); err == nil {
		t.Fatal("overwrote a key")
	}
	got, err := LoadKeyFile(path)
	if err != nil || got.Public() != k.Public() {
		t.Fatalf("loaded %v", err)
	}
	sealed, err := sealbox.SealCredentials(k.Public(), 1, 2, c)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := OpenCredentials(got, sealed, 1, 2); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(path, 0o640); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadKeyFile(path); err == nil || !strings.Contains(err.Error(), "0600") {
		t.Fatalf("group-readable key: %v", err)
	}
	junk := filepath.Join(dir, "junk.key")
	if err := os.WriteFile(junk, []byte(c.APISecret+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadKeyFile(junk); err == nil || strings.Contains(err.Error(), c.APISecret) {
		t.Fatalf("junk key: %v", err)
	}
	if _, err := ParsePrivateKey(c.APISecret); err == nil || strings.Contains(err.Error(), c.APISecret) {
		t.Fatalf("parse quoted the secret: %v", err)
	}
}

func TestNothingPrintsSecrets(t *testing.T) {
	k := mustKey(t)
	c := creds(t)
	j, _ := json.Marshal(struct {
		C sealbox.Credentials
		K *PrivateKey
	}{c, k})
	s := fmt.Sprintf("%v %+v %#v %s %x %s", k, k, k, k, k, j)
	if strings.Contains(s, c.APISecret) || strings.Contains(s, fmt.Sprint(k.priv)) {
		t.Fatal("printed a secret")
	}
}

func mustKey(t *testing.T) *PrivateKey {
	t.Helper()
	k, err := GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	return k
}

func vectorKey(t *testing.T) *PrivateKey {
	t.Helper()
	b := readVec(t, "recipient.priv")
	k, err := ParsePrivateKey(string(b))
	if err != nil {
		t.Fatal(err)
	}
	pub, err := sealbox.ParsePublicKey(string(readVec(t, "recipient.pub")))
	if err != nil || pub != k.Public() {
		t.Fatalf("vector public key: %v", err)
	}
	return k
}

func readVec(t *testing.T, name string) []byte {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("..", "testdata", name))
	if err != nil {
		t.Fatal(err)
	}
	return b
}
