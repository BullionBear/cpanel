// Package sealbox seals exchange credentials to the worker's public key (ARCHITECTURE §4.8, C5-02).
//
// The header matches mftiko's internal/platform/sealbox (E06-D013). cpanel does not import that package.
//
//	version (1 byte, 1) | fingerprint of the recipient key (8 bytes) | NaCl anonymous sealed box
//
// The fingerprint is the first 8 bytes of SHA-256 over the 32-byte X25519 public key. The box is
// X25519 and XSalsa20-Poly1305 (golang.org/x/crypto/nacl/box SealAnonymous). It has no associated
// data, so the opener compares exchange_account_id and credential_id with the row it was loaded for.
//
// Sealing needs only the public key, so cpanel-api may link this package. Opening is
// internal/platform/sealbox/open, which cpanel-api must not link (I6). A sealed credential stays in
// cpanel; the matching format is not a channel to mftiko.
//
// Nothing here logs. Credentials prints REDACTED, and errors name what failed, never the bytes.
package sealbox

import (
	"bytes"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"strings"

	"golang.org/x/crypto/nacl/box"
	"google.golang.org/protobuf/proto"
)

// Version is the header's first byte. Another algorithm gets another version.
const Version = 1

const (
	keySize        = 32
	fingerprintLen = 8
	headerLen      = 1 + fingerprintLen
)

var (
	// ErrOtherKey means the ciphertext was sealed to a different public key.
	ErrOtherKey = errors.New("sealbox: sealed to another key")
	// ErrUnreadable means the sealed bytes are empty, an unknown version, cut short, or damaged.
	ErrUnreadable = errors.New("sealbox: sealed credentials are unreadable")
	// ErrIDMismatch means the box opened, but the plaintext ids are not the ones the caller asked for.
	ErrIDMismatch = errors.New("sealbox: sealed for another account")
)

// PublicKey is the worker's X25519 public key. cpanel-api holds this and not the private key.
type PublicKey [keySize]byte

// String is the key in standard base64, the form of the key file.
func (p PublicKey) String() string { return base64.StdEncoding.EncodeToString(p[:]) }

// Fingerprint is the first 8 bytes of SHA-256 over the key, as the header carries it.
func (p PublicKey) Fingerprint() [fingerprintLen]byte {
	sum := p.SHA256()
	var f [fingerprintLen]byte
	copy(f[:], sum[:])
	return f
}

// SHA256 is the full digest of the 32-byte key. The header keeps only the first 8 bytes.
func (p PublicKey) SHA256() [sha256.Size]byte { return sha256.Sum256(p[:]) }

// SealedTo reports whether sealed's header names pub. It does not authenticate the box.
// A short buffer or another version is false.
func SealedTo(pub PublicKey, sealed []byte) bool {
	if len(sealed) < headerLen || sealed[0] != Version {
		return false
	}
	fp := pub.Fingerprint()
	return bytes.Equal(sealed[1:headerLen], fp[:])
}

// ParsePublicKey reads a key in standard base64. Surrounding space is ignored.
// The error does not quote the input.
func ParsePublicKey(s string) (PublicKey, error) {
	var p PublicKey
	b, err := base64.StdEncoding.DecodeString(strings.TrimSpace(s))
	if err != nil || len(b) != keySize {
		return p, errors.New("sealbox: a public key is 32 bytes in standard base64")
	}
	copy(p[:], b)
	return p, nil
}

// Credentials are an account's exchange key once opened. They print REDACTED.
type Credentials struct {
	APIKey     string
	APISecret  string
	Passphrase string
}

const redactedCreds = "sealbox.Credentials{REDACTED}"

func (Credentials) String() string               { return redactedCreds }
func (Credentials) GoString() string             { return redactedCreds }
func (Credentials) Format(f fmt.State, _ rune)   { _, _ = io.WriteString(f, redactedCreds) }
func (Credentials) MarshalJSON() ([]byte, error) { return []byte(`"REDACTED"`), nil }
func (Credentials) MarshalText() ([]byte, error) { return []byte("REDACTED"), nil }

// Seal seals plaintext to pub.
func Seal(pub PublicKey, plaintext []byte) ([]byte, error) {
	fp := pub.Fingerprint()
	out := make([]byte, 0, headerLen+len(plaintext)+box.AnonymousOverhead)
	out = append(out, Version)
	out = append(out, fp[:]...)
	p := [keySize]byte(pub)
	return box.SealAnonymous(out, plaintext, &p, rand.Reader)
}

// SealCredentials seals c for the exchange account and credential ids. The passphrase may be empty;
// the api key and the secret may not. The ids are sealed inside the box and checked again on open.
func SealCredentials(pub PublicKey, exchangeAccountID, credentialID int64, c Credentials) ([]byte, error) {
	if exchangeAccountID <= 0 || credentialID <= 0 {
		return nil, errors.New("sealbox: credentials need an exchange account id and a credential id")
	}
	if c.APIKey == "" || c.APISecret == "" {
		return nil, errors.New("sealbox: credentials need an api key and a secret")
	}
	plain, err := proto.Marshal(&ExchangeCredentials{
		ExchangeAccountId: exchangeAccountID,
		CredentialId:      credentialID,
		ApiKey:            c.APIKey,
		ApiSecret:         c.APISecret,
		Passphrase:        c.Passphrase,
	})
	if err != nil {
		return nil, errors.New("sealbox: credentials do not marshal")
	}
	defer clear(plain)
	return Seal(pub, plain)
}

func clear(b []byte) {
	for i := range b {
		b[i] = 0
	}
}
