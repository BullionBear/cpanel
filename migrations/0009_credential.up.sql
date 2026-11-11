-- Credentials (C5-03, #88, C5-D001).
--
-- purpose is only read: there is no purpose that hands a key to mftiko.
-- At most one unverified row and one active row per account. A new key may be
-- registered while one is active; it stays unverified until credential.verify
-- passes, and that pass disables the previous active row in the same transaction.
-- auth_failed is accepted so C5-04 can use it without widening this check.
-- consecutive_auth_failures stays 0 until that ticket.

CREATE TABLE pms.credential (
    id                          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY CHECK (id BETWEEN 1 AND 9007199254740991),
    exchange_account_id         BIGINT      NOT NULL REFERENCES pms.exchange_account (id),
    purpose                     TEXT        NOT NULL CHECK (purpose = 'read'),
    status                      TEXT        NOT NULL CHECK (status IN ('unverified', 'active', 'rejected', 'auth_failed', 'disabled')),
    ciphertext                  BYTEA       NOT NULL CHECK (octet_length(ciphertext) > 0),
    api_key_last4               TEXT        NOT NULL CHECK (char_length(api_key_last4) BETWEEN 1 AND 4 AND api_key_last4 = btrim(api_key_last4)),
    reported_uid                TEXT,
    permission_summary          TEXT,
    verified_at                 TIMESTAMPTZ,
    failure_reason              TEXT,
    consecutive_auth_failures   INTEGER     NOT NULL DEFAULT 0 CHECK (consecutive_auth_failures >= 0),
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX credential_one_unverified
    ON pms.credential (exchange_account_id)
    WHERE status = 'unverified' AND purpose = 'read';

CREATE UNIQUE INDEX credential_one_active
    ON pms.credential (exchange_account_id)
    WHERE status = 'active' AND purpose = 'read';
