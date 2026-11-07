// Package auth is Discord login, sessions, and bearer tokens. The database stores
// only the SHA-256 of a session id or token. Callers must not log those secrets.
package auth

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"errors"
	"strings"
	"time"
)

const (
	// CookieName is the session cookie. It is HttpOnly, Secure, and SameSite=Strict.
	CookieName = "cpanel_session"
	// StateCookie is the OAuth state cookie. It is HttpOnly, Secure, and SameSite=Lax
	// so the browser sends it on the top-level redirect back from Discord.
	StateCookie = "cpanel_oauth_state"
	// TokenPrefix marks a cpanel bearer token so a leaked string is recognizable.
	TokenPrefix = "cpanel_"

	// DefaultSessionTTL is how long a session cookie stays valid.
	DefaultSessionTTL = 12 * time.Hour
	// DefaultRoleRecheck is how long a stored role check may be reused.
	DefaultRoleRecheck = time.Hour
)

// User is a Discord account.
type User struct {
	ID          string
	Username    string
	DisplayName string
}

// Member is a Discord guild member.
type Member struct {
	Roles []string
}

var (
	// ErrNotMember means the Discord user is not in the configured guild.
	ErrNotMember = errors.New("not a guild member")
	// ErrNoRole means the member does not have the configured role.
	ErrNoRole = errors.New("missing role")
	// ErrUnavailable means Discord could not be reached or rejected the call.
	ErrUnavailable = errors.New("discord unavailable")
	// ErrNotFound means the session or token is missing, expired, or revoked.
	ErrNotFound = errors.New("not found")
)

// Settings is the Discord application and the public origin. Empty fields mean
// login is not configured. Secrets are never written to a log by this package.
type Settings struct {
	PublicOrigin  string
	ClientID      string
	ClientSecret  string
	BotToken      string
	GuildID       string
	RoleID        string
	RedirectURI   string
	APIBase       string
	AuthorizeBase string
}

// Origin is the public site origin without a trailing slash.
func (s Settings) Origin() string {
	return strings.TrimRight(strings.TrimSpace(s.PublicOrigin), "/")
}

// Redirect is the OAuth redirect URI.
func (s Settings) Redirect() string {
	if strings.TrimSpace(s.RedirectURI) != "" {
		return strings.TrimSpace(s.RedirectURI)
	}
	if s.Origin() == "" {
		return ""
	}
	return s.Origin() + "/auth/discord/callback"
}

// Configured reports whether a login can be started.
func (s Settings) Configured() bool {
	return s.ClientID != "" && s.ClientSecret != "" && s.BotToken != "" && s.GuildID != "" && s.RoleID != "" && s.Redirect() != ""
}

// Hash is the SHA-256 of a session id or bearer token.
func Hash(secret string) []byte {
	sum := sha256.Sum256([]byte(secret))
	return sum[:]
}

// SameHash reports whether a stored hash is the hash of secret.
func SameHash(stored []byte, secret string) bool {
	got := Hash(secret)
	return len(stored) == len(got) && subtle.ConstantTimeCompare(stored, got) == 1
}

// NewSecret returns a hex string of n bytes from crypto/rand.
func NewSecret(n int) (string, error) {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}

// NewToken returns a bearer token with TokenPrefix. The prefix is not a secret by itself.
func NewToken() (string, error) {
	raw, err := NewSecret(32)
	if err != nil {
		return "", err
	}
	return TokenPrefix + raw, nil
}
