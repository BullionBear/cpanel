// Package ed25519pem parses an unencrypted PKCS#8 Ed25519 private key.
// Binance gives operators that PEM and expects Ed25519 signatures of the query string.
// The error never includes the PEM.
package ed25519pem

import (
	"crypto/ed25519"
	"crypto/x509"
	"encoding/pem"
	"errors"
	"strings"
)

// PrivateKey parses an unencrypted PKCS#8 Ed25519 PEM. Encrypted keys, other algorithms,
// and anything that is not a PEM are the same error.
func PrivateKey(pemText string) (ed25519.PrivateKey, error) {
	block, _ := pem.Decode([]byte(strings.TrimSpace(pemText)))
	if block == nil || block.Type != "PRIVATE KEY" {
		return nil, errPEM
	}
	key, err := x509.ParsePKCS8PrivateKey(block.Bytes)
	if err != nil {
		return nil, errPEM
	}
	ed, ok := key.(ed25519.PrivateKey)
	if !ok {
		return nil, errPEM
	}
	return ed, nil
}

var errPEM = errors.New("ed25519 secret must be an unencrypted PKCS#8 PEM")
