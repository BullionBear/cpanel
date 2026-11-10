-- Exchange accounts and kinds (C5-01, #85). Decisions C4-D015, C4-D016, C5-D002, C5-D006.
--
-- An account is one (exchange, exch_account_uid). The unique pair includes soft-deleted rows,
-- so a deleted account keeps its id and the pair cannot be reused. label is required and mutable,
-- and it is not part of the pair. verified_at and last_error are left empty for C5-03.

CREATE TABLE pms.account_kind (
    kind       TEXT PRIMARY KEY,
    exchange   TEXT    NOT NULL CHECK (exchange IN ('binance', 'bybit', 'okx')),
    venues     TEXT[]  NOT NULL CHECK (cardinality(venues) >= 1),
    api_family TEXT    NOT NULL CHECK (api_family <> ''),
    in_scope   BOOLEAN NOT NULL
);

INSERT INTO pms.account_kind (kind, exchange, venues, api_family, in_scope) VALUES
    ('Binance',   'binance', ARRAY['Binance'],                         'api',  true),
    ('BinanceUM', 'binance', ARRAY['BinanceUM'],                       'fapi', true),
    ('BinanceCM', 'binance', ARRAY['BinanceCM'],                       'dapi', false),
    ('BinanceEO', 'binance', ARRAY['BinanceEO'],                       'eapi', false),
    ('BinancePM', 'binance', ARRAY['Binance', 'BinanceUM', 'BinanceCM'], 'papi', false),
    ('Bybit',     'bybit',   ARRAY['Bybit'],                           'v5',   true),
    ('Okx',       'okx',     ARRAY['Okx'],                             'v5',   true);

CREATE TABLE pms.exchange_account (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY CHECK (id BETWEEN 1 AND 9007199254740991),
    exchange         TEXT        NOT NULL CHECK (exchange IN ('binance', 'bybit', 'okx')),
    exch_account_uid TEXT        NOT NULL CHECK (exch_account_uid <> '' AND exch_account_uid = btrim(exch_account_uid)),
    label            TEXT        NOT NULL CHECK (label <> '' AND label = btrim(label)),
    revision         BIGINT      NOT NULL DEFAULT 1 CHECK (revision >= 1),
    deleted_at       TIMESTAMPTZ,
    UNIQUE (exchange, exch_account_uid)
);

CREATE TABLE pms.exchange_account_kind (
    exchange_account_id BIGINT NOT NULL REFERENCES pms.exchange_account (id),
    kind                TEXT   NOT NULL REFERENCES pms.account_kind (kind),
    disabled_at         TIMESTAMPTZ,
    verified_at         TIMESTAMPTZ,
    last_error          TEXT,
    PRIMARY KEY (exchange_account_id, kind)
);

-- A kind row has to belong to the account's exchange and be in scope. Both violations raise
-- check_violation (23514). C8-01 flips BinanceCM in_scope; this trigger reads the column.
CREATE FUNCTION pms.exchange_account_kind_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    acc_exchange  text;
    kind_exchange text;
    kind_in_scope boolean;
BEGIN
    SELECT exchange INTO acc_exchange
    FROM pms.exchange_account
    WHERE id = NEW.exchange_account_id;
    SELECT exchange, in_scope INTO kind_exchange, kind_in_scope
    FROM pms.account_kind
    WHERE kind = NEW.kind;
    IF acc_exchange IS NULL
       OR kind_exchange IS DISTINCT FROM acc_exchange
       OR kind_in_scope IS NOT TRUE THEN
        RAISE EXCEPTION 'kind % is not in scope for this exchange account', NEW.kind
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER exchange_account_kind_guard
    BEFORE INSERT OR UPDATE ON pms.exchange_account_kind
    FOR EACH ROW
    EXECUTE FUNCTION pms.exchange_account_kind_guard();
