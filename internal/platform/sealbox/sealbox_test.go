package sealbox

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
)

func fixture(tag string) string {
	b := make([]byte, 12)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return tag + "-" + hex.EncodeToString(b)
}

func pub(t *testing.T) PublicKey {
	t.Helper()
	var p PublicKey
	if _, err := rand.Read(p[:]); err != nil {
		t.Fatal(err)
	}
	// A random 32 bytes is a public key for sealing. It need not be a point we can open.
	return p
}

func TestParsePublicKey(t *testing.T) {
	var raw PublicKey
	if _, err := rand.Read(raw[:]); err != nil {
		t.Fatal(err)
	}
	got, err := ParsePublicKey("  " + raw.String() + "\n")
	if err != nil || got != raw {
		t.Fatalf("%v", err)
	}
	for _, s := range []string{"", "abc", raw.String()[:20], "not base64!!!"} {
		_, err := ParsePublicKey(s)
		if err == nil || (s != "" && strings.Contains(err.Error(), s)) {
			t.Errorf("parsed %q: %v", s, err)
		}
	}
	sum := raw.SHA256()
	fp := raw.Fingerprint()
	if !bytes.Equal(sum[:len(fp)], fp[:]) {
		t.Fatal("fingerprint is not the leading digest bytes")
	}
}

func TestSealHeader(t *testing.T) {
	p := pub(t)
	sealed, err := Seal(p, []byte("x"))
	if err != nil {
		t.Fatal(err)
	}
	if sealed[0] != Version || !SealedTo(p, sealed) {
		t.Fatalf("header %x", sealed[:headerLen])
	}
	var other PublicKey
	other[0] = p[0] ^ 1
	if SealedTo(other, sealed) || SealedTo(p, nil) || SealedTo(p, []byte{0, 1, 2, 3, 4, 5, 6, 7, 8}) {
		t.Fatal("header compare")
	}
	again, err := Seal(p, []byte("x"))
	if err != nil || bytes.Equal(again, sealed) {
		t.Fatal("sealing twice gave the same bytes")
	}
}

func TestSealCredentials(t *testing.T) {
	p := pub(t)
	c := Credentials{APIKey: fixture("key"), APISecret: fixture("sec"), Passphrase: fixture("pp")}
	sealed, err := SealCredentials(p, 7, 9, c)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(sealed), c.APIKey) || strings.Contains(string(sealed), c.APISecret) || strings.Contains(string(sealed), c.Passphrase) {
		t.Fatal("ciphertext contains a credential")
	}
	for _, bad := range []struct {
		account, credential int64
		c                   Credentials
	}{
		{0, 9, c},
		{7, 0, c},
		{7, 9, Credentials{APISecret: "s"}},
		{7, 9, Credentials{APIKey: "k"}},
	} {
		if _, err := SealCredentials(p, bad.account, bad.credential, bad.c); err == nil {
			t.Errorf("sealed %d %d", bad.account, bad.credential)
		}
	}
}

func TestNothingPrintsSecrets(t *testing.T) {
	c := Credentials{APIKey: fixture("key"), APISecret: fixture("sec"), Passphrase: fixture("pp")}
	j, _ := json.Marshal(c)
	s := fmt.Sprintf("%v %+v %#v %s %q %x %d %s", c, c, c, c, c, c, c, j)
	for _, v := range []string{c.APIKey, c.APISecret, c.Passphrase} {
		if strings.Contains(s, v) {
			t.Fatalf("printed %q", v)
		}
	}
}
