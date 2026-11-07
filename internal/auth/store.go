package auth

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Session is a stored login. It does not include the cookie value.
type Session struct {
	DiscordUserID string
	Username      string
	DisplayName   string
	ExpiresAt     time.Time
	RoleCheckedAt time.Time
}

// Token is a stored bearer token. It does not include the secret.
type Token struct {
	ID            string
	Name          string
	DiscordUserID string
	Username      string
	CreatedAt     time.Time
	LastUsedAt    *time.Time
}

// Store reads and writes sessions, tokens, and audit rows.
type Store struct {
	Pool *pgxpool.Pool
	Now  func() time.Time
}

func (s *Store) now() time.Time {
	if s.Now != nil {
		return s.Now().UTC()
	}
	return time.Now().UTC()
}

// CreateSession stores the hash of rawID.
func (s *Store) CreateSession(ctx context.Context, rawID string, user User, ttl time.Duration) error {
	now := s.now()
	_, err := s.Pool.Exec(ctx, `
		INSERT INTO meta.session (id_hash, discord_user_id, username, display_name, created_at, expires_at, role_checked_at)
		VALUES ($1, $2, $3, $4, $5, $6, $5)`,
		Hash(rawID), user.ID, user.Username, user.DisplayName, now, now.Add(ttl))
	return err
}

// Session loads the session for rawID. An expired row is deleted.
func (s *Store) Session(ctx context.Context, rawID string) (Session, error) {
	var sess Session
	err := s.Pool.QueryRow(ctx, `
		SELECT discord_user_id, username, display_name, expires_at, role_checked_at
		FROM meta.session WHERE id_hash = $1`, Hash(rawID)).
		Scan(&sess.DiscordUserID, &sess.Username, &sess.DisplayName, &sess.ExpiresAt, &sess.RoleCheckedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return Session{}, ErrNotFound
	}
	if err != nil {
		return Session{}, err
	}
	if !sess.ExpiresAt.After(s.now()) {
		_ = s.DeleteSession(ctx, rawID)
		return Session{}, ErrNotFound
	}
	return sess, nil
}

// TouchRole records a successful role check.
func (s *Store) TouchRole(ctx context.Context, rawID string) error {
	_, err := s.Pool.Exec(ctx, `UPDATE meta.session SET role_checked_at = $2 WHERE id_hash = $1`, Hash(rawID), s.now())
	return err
}

// DeleteSession removes the session for rawID.
func (s *Store) DeleteSession(ctx context.Context, rawID string) error {
	_, err := s.Pool.Exec(ctx, `DELETE FROM meta.session WHERE id_hash = $1`, Hash(rawID))
	return err
}

// CreateToken stores the hash of rawToken and returns the public row.
func (s *Store) CreateToken(ctx context.Context, discordUserID, username, name, rawToken string) (Token, error) {
	id, err := NewSecret(16)
	if err != nil {
		return Token{}, err
	}
	now := s.now()
	_, err = s.Pool.Exec(ctx, `
		INSERT INTO meta.api_token (id, token_hash, name, discord_user_id, username, created_at)
		VALUES ($1, $2, $3, $4, $5, $6)`,
		id, Hash(rawToken), name, discordUserID, username, now)
	if err != nil {
		return Token{}, err
	}
	return Token{ID: id, Name: name, DiscordUserID: discordUserID, Username: username, CreatedAt: now}, nil
}

// Tokens lists the caller's tokens that are not revoked.
func (s *Store) Tokens(ctx context.Context, discordUserID string) ([]Token, error) {
	rows, err := s.Pool.Query(ctx, `
		SELECT id, name, discord_user_id, username, created_at, last_used_at
		FROM meta.api_token
		WHERE discord_user_id = $1 AND revoked_at IS NULL
		ORDER BY created_at`, discordUserID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Token
	for rows.Next() {
		var tok Token
		if err := rows.Scan(&tok.ID, &tok.Name, &tok.DiscordUserID, &tok.Username, &tok.CreatedAt, &tok.LastUsedAt); err != nil {
			return nil, err
		}
		out = append(out, tok)
	}
	return out, rows.Err()
}

// UseToken loads the token for rawToken and stamps last_used_at. Revoked tokens are missing.
func (s *Store) UseToken(ctx context.Context, rawToken string) (Token, error) {
	var tok Token
	err := s.Pool.QueryRow(ctx, `
		UPDATE meta.api_token SET last_used_at = $2
		WHERE token_hash = $1 AND revoked_at IS NULL
		RETURNING id, name, discord_user_id, username, created_at, last_used_at`,
		Hash(rawToken), s.now()).
		Scan(&tok.ID, &tok.Name, &tok.DiscordUserID, &tok.Username, &tok.CreatedAt, &tok.LastUsedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return Token{}, ErrNotFound
	}
	return tok, err
}

// RevokeToken marks the caller's token revoked. A missing or already revoked id is ErrNotFound.
func (s *Store) RevokeToken(ctx context.Context, discordUserID, id string) error {
	tag, err := s.Pool.Exec(ctx, `
		UPDATE meta.api_token SET revoked_at = $3
		WHERE id = $1 AND discord_user_id = $2 AND revoked_at IS NULL`,
		id, discordUserID, s.now())
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// Audit writes one meta.audit row. Secret fields inside params and result are masked.
func (s *Store) Audit(ctx context.Context, actor, action, target string, params, result any, requestID string) error {
	p, err := maskedJSON(params)
	if err != nil {
		return err
	}
	r, err := maskedJSON(result)
	if err != nil {
		return err
	}
	_, err = s.Pool.Exec(ctx, `
		INSERT INTO meta.audit (actor, action, target, params, result, request_id)
		VALUES ($1, $2, $3, $4, $5, $6)`,
		actor, action, target, p, r, requestID)
	return err
}

func maskedJSON(v any) ([]byte, error) {
	if v == nil {
		return nil, nil
	}
	raw, err := json.Marshal(v)
	if err != nil {
		return nil, err
	}
	var decoded any
	if err := json.Unmarshal(raw, &decoded); err != nil {
		return nil, err
	}
	maskValue(decoded)
	return json.Marshal(decoded)
}
