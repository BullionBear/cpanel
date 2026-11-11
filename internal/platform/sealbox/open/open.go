// Package open opens sealed exchange credentials with the worker's private key (ARCHITECTURE §4.8).
//
// cpanel-api holds no key that can open a secret (I6). internal/archtest fails if cmd/cpanel-api
// links this package. The sealed bytes stay in cpanel; opening them does not send them to mftiko.
package open

import (
	"bytes"
	"crypto/rand"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"

	"golang.org/x/crypto/curve25519"
	"golang.org/x/crypto/nacl/box"
	"google.golang.org/protobuf/proto"

	"github.com/BullionBear/cpanel/internal/platform/sealbox"
)

const keySize = 32

// PrivateKey is the worker's X25519 key pair. It prints REDACTED.
type PrivateKey struct {
	pub  sealbox.PublicKey
	priv [keySize]byte
}

// Public is the key to seal to.
func (k *PrivateKey) Public() sealbox.PublicKey {
	if k == nil {
		return sealbox.PublicKey{}
	}
	return k.pub
}

const redactedKey = "sealbox.PrivateKey{REDACTED}"

func (*PrivateKey) String() string               { return redactedKey }
func (*PrivateKey) GoString() string             { return redactedKey }
func (*PrivateKey) Format(f fmt.State, _ rune)   { _, _ = io.WriteString(f, redactedKey) }
func (*PrivateKey) MarshalJSON() ([]byte, error) { return []byte(`"REDACTED"`), nil }
func (*PrivateKey) MarshalText() ([]byte, error) { return []byte("REDACTED"), nil }

// GenerateKey makes a new key pair.
func GenerateKey() (*PrivateKey, error) {
	pub, priv, err := box.GenerateKey(rand.Reader)
	if err != nil {
		return nil, err
	}
	k := &PrivateKey{priv: *priv}
	copy(k.pub[:], pub[:])
	return k, nil
}

// ParsePrivateKey reads a private key in standard base64 and derives its public key.
// Surrounding space is ignored. The error does not quote the input.
func ParsePrivateKey(s string) (*PrivateKey, error) {
	raw, err := base64.StdEncoding.DecodeString(strings.TrimSpace(s))
	if err != nil || len(raw) != keySize {
		clear(raw)
		return nil, errors.New("sealbox: a private key is 32 bytes in standard base64")
	}
	k := &PrivateKey{}
	copy(k.priv[:], raw)
	clear(raw)
	pub, err := publicOf(&k.priv)
	if err != nil {
		return nil, err
	}
	k.pub = pub
	return k, nil
}

// CheckKeyFile refuses a path that is not a regular file, or that the group or others can read
// or write. The private key file is 0600 (C2-D008).
func CheckKeyFile(path string) error {
	fi, err := os.Stat(path)
	if err != nil {
		return fmt.Errorf("sealbox: %w", err)
	}
	if !fi.Mode().IsRegular() {
		return fmt.Errorf("sealbox: %s is not a regular file", path)
	}
	if fi.Mode().Perm()&0o077 != 0 {
		return fmt.Errorf("sealbox: %s is mode %04o; the key must be 0600", path, fi.Mode().Perm())
	}
	return nil
}

// LoadKeyFile reads the private key at path. It refuses a file the group or others can read or write.
func LoadKeyFile(path string) (*PrivateKey, error) {
	if err := CheckKeyFile(path); err != nil {
		return nil, err
	}
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("sealbox: %w", err)
	}
	k, err := ParsePrivateKey(string(b))
	clear(b)
	if err != nil {
		return nil, fmt.Errorf("sealbox: %s does not hold a 32-byte key in standard base64", path)
	}
	return k, nil
}

// WriteKeyFile writes k's private key to path, mode 0600, in standard base64. It refuses a path that
// exists: a key is replaced only by moving the old one away first, since every credential sealed to
// it has to be resealed (C5-05).
func WriteKeyFile(path string, k *PrivateKey) error {
	if k == nil {
		return errors.New("sealbox: no private key")
	}
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return fmt.Errorf("sealbox: %w", err)
	}
	_, err = io.WriteString(f, base64.StdEncoding.EncodeToString(k.priv[:])+"\n")
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		return fmt.Errorf("sealbox: write %s: %w", path, err)
	}
	return nil
}

// Open opens what sealbox.Seal made for k.
func Open(k *PrivateKey, sealed []byte) ([]byte, error) {
	if k == nil || len(sealed) < sealboxHeaderLen()+box.AnonymousOverhead || sealed[0] != sealbox.Version {
		return nil, sealbox.ErrUnreadable
	}
	fp := k.pub.Fingerprint()
	if !bytes.Equal(sealed[1:1+len(fp)], fp[:]) {
		return nil, sealbox.ErrOtherKey
	}
	pub := [keySize]byte(k.pub)
	out, ok := box.OpenAnonymous(nil, sealed[1+len(fp):], &pub, &k.priv)
	if !ok {
		return nil, sealbox.ErrUnreadable
	}
	return out, nil
}

// OpenCredentials opens sealed with k and checks that it was sealed for the two ids.
// A mismatch is a failed open: the api key is not returned.
func OpenCredentials(k *PrivateKey, sealed []byte, exchangeAccountID, credentialID int64) (sealbox.Credentials, error) {
	plain, err := Open(k, sealed)
	if err != nil {
		return sealbox.Credentials{}, err
	}
	defer clear(plain)
	var msg sealbox.ExchangeCredentials
	if err := proto.Unmarshal(plain, &msg); err != nil {
		return sealbox.Credentials{}, sealbox.ErrUnreadable
	}
	// A non-positive id is not a credential. Rejecting it also refuses a plaintext whose
	// credential_id was missing, which is what a different protobuf leaves in that field.
	if exchangeAccountID <= 0 || credentialID <= 0 || msg.ExchangeAccountId != exchangeAccountID || msg.CredentialId != credentialID {
		return sealbox.Credentials{}, sealbox.ErrIDMismatch
	}
	if msg.ApiKey == "" || msg.ApiSecret == "" {
		return sealbox.Credentials{}, sealbox.ErrUnreadable
	}
	return sealbox.Credentials{APIKey: msg.ApiKey, APISecret: msg.ApiSecret, Passphrase: msg.Passphrase}, nil
}

func sealboxHeaderLen() int { return 1 + len(sealbox.PublicKey{}.Fingerprint()) }

func publicOf(priv *[keySize]byte) (sealbox.PublicKey, error) {
	var p sealbox.PublicKey
	b, err := curve25519.X25519(priv[:], curve25519.Basepoint)
	if err != nil {
		return p, errors.New("sealbox: the key file does not hold a usable X25519 key")
	}
	copy(p[:], b)
	return p, nil
}

func clear(b []byte) {
	for i := range b {
		b[i] = 0
	}
}
