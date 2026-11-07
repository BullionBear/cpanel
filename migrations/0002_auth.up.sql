-- Sessions and API tokens (C2-04). The cookie and the bearer token are random secrets.
-- Only their SHA-256 is stored. Logout deletes the session row. Revoke sets revoked_at.

CREATE TABLE meta.session (
    id_hash         BYTEA PRIMARY KEY,
    discord_user_id TEXT        NOT NULL,
    username        TEXT        NOT NULL,
    display_name    TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    role_checked_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE meta.api_token (
    id              TEXT PRIMARY KEY,
    token_hash      BYTEA       NOT NULL UNIQUE,
    name            TEXT        NOT NULL,
    discord_user_id TEXT        NOT NULL,
    username        TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL,
    last_used_at    TIMESTAMPTZ,
    revoked_at      TIMESTAMPTZ
);
