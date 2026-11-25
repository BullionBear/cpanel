// Package exchangeenv is the deployment's exchange environment (C7-D002).
//
// prod and testnet are different deployments and different databases. A process
// records the environment on each credential it creates. A worker refuses a
// credential from the other environment and does not call the exchange.
//
// Starting as testnet also requires the allow flag. A production configuration
// that only flips the environment does not start.
package exchangeenv

import (
	"errors"
	"fmt"
	"strings"
)

const (
	// Prod is the default. Public Binance hosts.
	Prod = "prod"
	// Testnet is Binance's futures and spot testnet. It needs Allow.
	Testnet = "testnet"

	// Allow is the only value of the allow flag that permits testnet.
	Allow = "1"

	// KeyHMAC is the default key. An empty sealed key_type is this.
	KeyHMAC = "hmac"
	// KeyEd25519 is a Binance Ed25519 key. The secret is an unencrypted PKCS#8 PEM.
	KeyEd25519 = "ed25519"
)

// Resolve returns prod or testnet. env empty means prod. testnet is refused
// unless allow is Allow. Anything else is an error. The error does not repeat a secret.
func Resolve(env, allow string) (string, error) {
	env = strings.TrimSpace(env)
	if env == "" {
		env = Prod
	}
	switch env {
	case Prod:
		return Prod, nil
	case Testnet:
		if strings.TrimSpace(allow) != Allow {
			return "", errors.New("testnet requires -allow-testnet or CPANEL_ALLOW_TESTNET=1; a production configuration cannot turn testnet on")
		}
		return Testnet, nil
	default:
		return "", fmt.Errorf("exchange env must be prod or testnet, got %q", env)
	}
}

// Normalize maps an empty environment to prod.
func Normalize(env string) string {
	if strings.TrimSpace(env) == "" || env == Prod {
		return Prod
	}
	return env
}

// Same reports whether a process and a stored credential are the same environment.
// Empty is prod.
func Same(process, stored string) bool {
	return Normalize(process) == Normalize(stored)
}

// KeyType maps an empty sealed value to hmac.
func KeyType(v string) string {
	switch strings.TrimSpace(v) {
	case "", KeyHMAC:
		return KeyHMAC
	default:
		return strings.TrimSpace(v)
	}
}
