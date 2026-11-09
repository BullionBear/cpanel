-- Symbol mirror (C4-02, #76). Decisions C4-D010, C4-D011.
--
-- mirror.symbol is a copy of SYM's instruments. Rows are never deleted: a delisting sets
-- active = false and keeps the uid. (venue, category, exch_ticker) is not unique, because a
-- delisted instrument and the one that relists it share a ticker and not a uid. venue and
-- category are stored as SYM spells them.

CREATE TABLE mirror.symbol (
    uid                BIGINT PRIMARY KEY CHECK (uid BETWEEN 1 AND 4294967295),
    venue              TEXT        NOT NULL,
    category           TEXT        NOT NULL,
    exch_ticker        TEXT        NOT NULL,
    universal_ticker   TEXT        NOT NULL,
    base               TEXT        NOT NULL,
    quote              TEXT        NOT NULL,
    settle             TEXT,
    contract_size      NUMERIC,
    contract_value_ccy TEXT,
    price_ccy          TEXT,
    expiry             TIMESTAMPTZ,
    strike             NUMERIC,
    option_type        TEXT,
    status             TEXT        NOT NULL,
    active             BOOLEAN     NOT NULL,
    filters            JSONB       NOT NULL DEFAULT '{}',
    sym_updated_at     TIMESTAMPTZ,
    first_seen_at      TIMESTAMPTZ NOT NULL,
    last_seen_at       TIMESTAMPTZ NOT NULL,
    inactive_since     TIMESTAMPTZ
);

CREATE INDEX symbol_venue_category_ticker ON mirror.symbol (venue, category, exch_ticker);
