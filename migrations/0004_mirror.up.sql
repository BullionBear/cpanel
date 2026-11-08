-- Account mirror (C4-01, #75). Decisions C4-D009, C4-D010, C4-D013, C4-D017.
--
-- mirror.account is a copy of mftiko's accounts, including soft-deleted ones and kinds cpanel
-- does not trade yet. Rows are never deleted. exchange and exch_account_uid are stored as mftiko
-- sent them, and once set they are not overwritten (C4-D013). identity_conflict keeps a value
-- that arrived later and was refused. The raw payload is not stored.

CREATE TABLE mirror.account (
    api_id             BIGINT PRIMARY KEY CHECK (api_id BETWEEN 1 AND 9007199254740991),
    name               TEXT        NOT NULL,
    kind               TEXT        NOT NULL,
    exchange           TEXT,
    exch_account_uid   TEXT,
    account_revision   BIGINT      NOT NULL,
    deleted_at         TIMESTAMPTZ,
    first_seen_at      TIMESTAMPTZ NOT NULL,
    last_seen_at       TIMESTAMPTZ NOT NULL,
    identity_conflict  JSONB
);

-- Not unique: one exchange UID is often registered as several kinds.
CREATE INDEX account_exchange_uid ON mirror.account (exchange, exch_account_uid);

-- One row per mirror source. last_ok_at is a round with no error. last_complete_at is a round
-- that read every page and had no unavailable region (C4-D009). revision and remote_revision
-- are for SYM's tables; account sync leaves them null.
CREATE TABLE mirror.sync_state (
    source                TEXT PRIMARY KEY,
    last_attempt_at       TIMESTAMPTZ,
    last_ok_at            TIMESTAMPTZ,
    last_complete_at      TIMESTAMPTZ,
    items                 BIGINT,
    unavailable_regions   TEXT[] NOT NULL DEFAULT '{}',
    revision              BIGINT,
    remote_revision       BIGINT
);
