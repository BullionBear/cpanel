-- History raw and ledger tables (C7-01, #101). Decision C7-D003.
--
-- raw.venue_trade, raw.venue_order and raw.venue_income are immutable and
-- partitioned by month of fetched_at, like raw.venue_snapshot. A unique index
-- on a partitioned table has to include the partition key (ARCHITECTURE §4.4),
-- so (exchange_account_id, venue, native_id) lives in an unpartitioned key
-- table. Ledger rows point at that key and can be deleted and rewritten.

CREATE FUNCTION raw.history_parent(rel name) RETURNS text
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    parent text := rel::text;
BEGIN
    IF parent LIKE '%\_default' ESCAPE '\' THEN
        parent := left(parent, length(parent) - length('_default'));
    ELSIF parent ~ '_[0-9]{6}$' THEN
        parent := regexp_replace(parent, '_[0-9]{6}$', '');
    END IF;
    IF parent NOT IN ('venue_trade', 'venue_order', 'venue_income') THEN
        RAISE EXCEPTION 'raw history trigger on %', rel;
    END IF;
    RETURN parent;
END;
$$;

-- UPDATE and DELETE are rejected. ensure_month_partition sets
-- cpanel.<parent>_moving for the DELETE that moves rows out of the default
-- partition, then clears it.
CREATE FUNCTION raw.reject_history_change() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    parent text := raw.history_parent(TG_TABLE_NAME);
BEGIN
    IF TG_OP = 'DELETE' AND current_setting('cpanel.' || parent || '_moving', true) = 'on' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'raw.% is immutable', parent;
END;
$$;

-- The business key. A partition move inserts the row again before the trigger
-- exists on the new partition; once attached, a move sets the moving flag and
-- this insert is skipped so the key is not duplicated.
CREATE FUNCTION raw.venue_history_key() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    parent text := raw.history_parent(TG_TABLE_NAME);
BEGIN
    IF current_setting('cpanel.' || parent || '_moving', true) = 'on' THEN
        RETURN NEW;
    END IF;
    EXECUTE format(
        'INSERT INTO raw.%I (exchange_account_id, venue, native_id) VALUES ($1, $2, $3)',
        parent || '_key')
        USING NEW.exchange_account_id, NEW.venue, NEW.native_id;
    RETURN NEW;
END;
$$;

CREATE TABLE raw.venue_trade_key (
    exchange_account_id BIGINT NOT NULL REFERENCES pms.exchange_account (id),
    venue               TEXT   NOT NULL CHECK (venue <> ''),
    native_id           TEXT   NOT NULL CHECK (native_id <> ''),
    PRIMARY KEY (exchange_account_id, venue, native_id)
);

CREATE TABLE raw.venue_order_key (
    exchange_account_id BIGINT NOT NULL REFERENCES pms.exchange_account (id),
    venue               TEXT   NOT NULL CHECK (venue <> ''),
    native_id           TEXT   NOT NULL CHECK (native_id <> ''),
    PRIMARY KEY (exchange_account_id, venue, native_id)
);

CREATE TABLE raw.venue_income_key (
    exchange_account_id BIGINT NOT NULL REFERENCES pms.exchange_account (id),
    venue               TEXT   NOT NULL CHECK (venue <> ''),
    native_id           TEXT   NOT NULL CHECK (native_id <> ''),
    PRIMARY KEY (exchange_account_id, venue, native_id)
);

CREATE SEQUENCE raw.venue_trade_id_seq;

CREATE TABLE raw.venue_trade (
    id                  BIGINT      NOT NULL DEFAULT nextval('raw.venue_trade_id_seq'),
    exchange_account_id BIGINT      NOT NULL REFERENCES pms.exchange_account (id),
    venue               TEXT        NOT NULL CHECK (venue <> ''),
    native_id           TEXT        NOT NULL CHECK (native_id <> ''),
    params              JSONB       NOT NULL,
    payload             JSONB       NOT NULL,
    fetched_at          TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (fetched_at, id)
) PARTITION BY RANGE (fetched_at);

ALTER SEQUENCE raw.venue_trade_id_seq OWNED BY raw.venue_trade.id;

CREATE TABLE raw.venue_trade_default PARTITION OF raw.venue_trade DEFAULT;

CREATE TRIGGER venue_trade_immutable
    BEFORE UPDATE OR DELETE ON raw.venue_trade
    FOR EACH ROW EXECUTE FUNCTION raw.reject_history_change();

CREATE TRIGGER venue_trade_key
    BEFORE INSERT ON raw.venue_trade
    FOR EACH ROW EXECUTE FUNCTION raw.venue_history_key();

CREATE SEQUENCE raw.venue_order_id_seq;

CREATE TABLE raw.venue_order (
    id                  BIGINT      NOT NULL DEFAULT nextval('raw.venue_order_id_seq'),
    exchange_account_id BIGINT      NOT NULL REFERENCES pms.exchange_account (id),
    venue               TEXT        NOT NULL CHECK (venue <> ''),
    native_id           TEXT        NOT NULL CHECK (native_id <> ''),
    params              JSONB       NOT NULL,
    payload             JSONB       NOT NULL,
    fetched_at          TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (fetched_at, id)
) PARTITION BY RANGE (fetched_at);

ALTER SEQUENCE raw.venue_order_id_seq OWNED BY raw.venue_order.id;

CREATE TABLE raw.venue_order_default PARTITION OF raw.venue_order DEFAULT;

CREATE TRIGGER venue_order_immutable
    BEFORE UPDATE OR DELETE ON raw.venue_order
    FOR EACH ROW EXECUTE FUNCTION raw.reject_history_change();

CREATE TRIGGER venue_order_key
    BEFORE INSERT ON raw.venue_order
    FOR EACH ROW EXECUTE FUNCTION raw.venue_history_key();

CREATE SEQUENCE raw.venue_income_id_seq;

CREATE TABLE raw.venue_income (
    id                  BIGINT      NOT NULL DEFAULT nextval('raw.venue_income_id_seq'),
    exchange_account_id BIGINT      NOT NULL REFERENCES pms.exchange_account (id),
    venue               TEXT        NOT NULL CHECK (venue <> ''),
    native_id           TEXT        NOT NULL CHECK (native_id <> ''),
    params              JSONB       NOT NULL,
    payload             JSONB       NOT NULL,
    fetched_at          TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (fetched_at, id)
) PARTITION BY RANGE (fetched_at);

ALTER SEQUENCE raw.venue_income_id_seq OWNED BY raw.venue_income.id;

CREATE TABLE raw.venue_income_default PARTITION OF raw.venue_income DEFAULT;

CREATE TRIGGER venue_income_immutable
    BEFORE UPDATE OR DELETE ON raw.venue_income
    FOR EACH ROW EXECUTE FUNCTION raw.reject_history_change();

CREATE TRIGGER venue_income_key
    BEFORE INSERT ON raw.venue_income
    FOR EACH ROW EXECUTE FUNCTION raw.venue_history_key();

-- One function for every monthly raw table, including venue_snapshot.
-- cpanel.<parent>_moving lets the default-partition move delete a row.
-- Partitions are never dropped: raw is kept forever.
CREATE FUNCTION raw.ensure_month_partition(parent text, month date) RETURNS boolean
LANGUAGE plpgsql AS $$
DECLARE
    start_month date;
    part        text;
    lo          timestamptz;
    hi          timestamptz;
    moving      text;
BEGIN
    IF parent NOT IN ('venue_snapshot', 'venue_trade', 'venue_order', 'venue_income') THEN
        RAISE EXCEPTION 'unknown raw parent %', parent;
    END IF;
    start_month := date_trunc('month', month)::date;
    part := parent || '_' || to_char(start_month, 'YYYYMM');
    lo := start_month::timestamp AT TIME ZONE 'UTC';
    hi := (start_month + interval '1 month')::timestamp AT TIME ZONE 'UTC';
    IF to_regclass(format('raw.%I', part)) IS NOT NULL THEN
        RETURN false;
    END IF;
    EXECUTE format('CREATE TABLE raw.%I (LIKE raw.%I INCLUDING DEFAULTS INCLUDING CONSTRAINTS)', part, parent);
    EXECUTE format('ALTER TABLE raw.%I ADD CHECK (fetched_at >= %L AND fetched_at < %L)', part, lo, hi);
    moving := 'cpanel.' || parent || '_moving';
    PERFORM set_config(moving, 'on', true);
    EXECUTE format(
        'WITH moved AS (DELETE FROM raw.%I WHERE fetched_at >= %L AND fetched_at < %L RETURNING *) INSERT INTO raw.%I SELECT * FROM moved',
        parent || '_default', lo, hi, part);
    PERFORM set_config(moving, 'off', true);
    EXECUTE format('ALTER TABLE raw.%I ATTACH PARTITION raw.%I FOR VALUES FROM (%L) TO (%L)', parent, part, lo, hi);
    RETURN true;
END;
$$;

CREATE OR REPLACE FUNCTION raw.ensure_venue_snapshot_partition(month date) RETURNS boolean
LANGUAGE sql AS $$
    SELECT raw.ensure_month_partition('venue_snapshot', month);
$$;

CREATE TABLE ledger.trade (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    exchange_account_id BIGINT      NOT NULL,
    venue               TEXT        NOT NULL,
    native_id           TEXT        NOT NULL,
    uid                 BIGINT      NOT NULL CHECK (uid BETWEEN 1 AND 4294967295),
    order_native_id     TEXT        NOT NULL CHECK (order_native_id <> ''),
    price               NUMERIC     NOT NULL,
    qty                 NUMERIC     NOT NULL,
    quote_qty           NUMERIC     NOT NULL,
    side                TEXT        NOT NULL CHECK (side <> ''),
    position_side       TEXT        NOT NULL CHECK (position_side <> ''),
    traded_at           TIMESTAMPTZ NOT NULL,
    commission          NUMERIC     NOT NULL,
    commission_asset    TEXT        NOT NULL CHECK (commission_asset <> ''),
    realized_pnl        NUMERIC     NOT NULL,
    norm_version        INTEGER     NOT NULL CHECK (norm_version >= 1),
    UNIQUE (exchange_account_id, venue, native_id),
    FOREIGN KEY (exchange_account_id, venue, native_id) REFERENCES raw.venue_trade_key
);

-- Finished orders with a fill only (D8). cloid fields are all set or all null.
CREATE TABLE ledger.order (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    exchange_account_id BIGINT      NOT NULL,
    venue               TEXT        NOT NULL,
    native_id           TEXT        NOT NULL,
    uid                 BIGINT      NOT NULL CHECK (uid BETWEEN 1 AND 4294967295),
    client_order_id     TEXT        CHECK (client_order_id IS NULL OR client_order_id <> ''),
    cloid_ver           INTEGER,
    algo                TEXT,
    cloid_ts_ms         BIGINT,
    executed_qty        NUMERIC     NOT NULL CHECK (executed_qty > 0),
    norm_version        INTEGER     NOT NULL CHECK (norm_version >= 1),
    UNIQUE (exchange_account_id, venue, native_id),
    FOREIGN KEY (exchange_account_id, venue, native_id) REFERENCES raw.venue_order_key,
    CHECK (
        (cloid_ver IS NULL AND algo IS NULL AND cloid_ts_ms IS NULL)
        OR (cloid_ver IS NOT NULL AND algo IS NOT NULL AND algo <> '' AND cloid_ts_ms IS NOT NULL)
    )
);

CREATE INDEX order_cloid ON ledger.order (exchange_account_id, algo, cloid_ts_ms);

-- uid is null when the income row has no symbol.
CREATE TABLE ledger.funding (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    exchange_account_id BIGINT      NOT NULL,
    venue               TEXT        NOT NULL,
    native_id           TEXT        NOT NULL,
    uid                 BIGINT      CHECK (uid IS NULL OR uid BETWEEN 1 AND 4294967295),
    asset               TEXT        NOT NULL CHECK (asset <> ''),
    income              NUMERIC     NOT NULL,
    funded_at           TIMESTAMPTZ NOT NULL,
    norm_version        INTEGER     NOT NULL CHECK (norm_version >= 1),
    UNIQUE (exchange_account_id, venue, native_id),
    FOREIGN KEY (exchange_account_id, venue, native_id) REFERENCES raw.venue_income_key
);
