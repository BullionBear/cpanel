// Package open will open the sealed exchange-key secrets with the worker's private key (ARCHITECTURE §4.8). It
// arrives with C5; sealing, which needs only the public key, will be a separate package.
//
// cpanel-api holds no key that can open a secret (I6). internal/archtest fails if cmd/cpanel-api links this package.
package open
