-- Scope mirror (C4-04, #77). Decisions C4-D009, C4-D010, C4-D017.
--
-- mirror.scope is a copy of mftiko's GET /scopes. There is no foreign key to mirror.account:
-- a scope can arrive before the account that owns it. Rows are never deleted, and since only
-- moves earlier. BinancePM and BinanceEO scopes are stored too. since is truncated down to
-- microseconds before it is written. The raw payload is not stored.

CREATE TABLE mirror.scope (
    api_id           BIGINT      NOT NULL CHECK (api_id BETWEEN 1 AND 9007199254740991),
    uid              BIGINT      NOT NULL CHECK (uid BETWEEN 1 AND 4294967295),
    since            TIMESTAMPTZ NOT NULL,
    first_seen_at    TIMESTAMPTZ NOT NULL,
    since_lowered_at TIMESTAMPTZ,
    last_seen_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (api_id, uid)
);

CREATE INDEX scope_uid ON mirror.scope (uid);
