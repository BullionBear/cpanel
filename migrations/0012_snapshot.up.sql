-- Snapshot tables, the hourly gap query, and monthly raw partitions (C6-02, #100).
-- Decisions C6-D004, C6-D005. Column names follow docs/venues/binance-um.md.
--
-- An empty account still gets a snapshot_batch, or "latest" would read the previous
-- non-empty result. Past hours are not backfilled. raw.venue_snapshot is immutable
-- and kept forever: maintenance only makes the coming months.

CREATE TABLE ledger.snapshot_batch (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    exchange_account_id BIGINT      NOT NULL REFERENCES pms.exchange_account (id),
    bucket              TIMESTAMPTZ NOT NULL,
    captured_at         TIMESTAMPTZ NOT NULL,
    UNIQUE (exchange_account_id, bucket),
    CHECK (bucket = (date_trunc('hour', bucket AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'))
);

-- One row per kind the account had enabled when the batch was written.
-- succeeded: the worker captured this kind. failed: the exchange call failed.
-- unimplemented: the kind is enabled and this worker has no snapshot for it.
CREATE TABLE ledger.snapshot_batch_kind (
    batch_id BIGINT NOT NULL REFERENCES ledger.snapshot_batch (id),
    kind     TEXT   NOT NULL REFERENCES pms.account_kind (kind),
    status   TEXT   NOT NULL CHECK (status IN ('succeeded', 'failed', 'unimplemented')),
    PRIMARY KEY (batch_id, kind)
);

-- BinanceUM wallet is the text 'um' (C6-D005). There is no locked column.
CREATE TABLE ledger.balance_snapshot (
    batch_id              BIGINT  NOT NULL REFERENCES ledger.snapshot_batch (id),
    kind                  TEXT    NOT NULL,
    wallet                TEXT    NOT NULL CHECK (wallet <> ''),
    asset                 TEXT    NOT NULL CHECK (asset <> ''),
    balance               NUMERIC NOT NULL,
    available_balance     NUMERIC NOT NULL,
    cross_wallet_balance  NUMERIC NOT NULL,
    max_withdraw_amount   NUMERIC NOT NULL,
    norm_version          INTEGER NOT NULL DEFAULT 1 CHECK (norm_version >= 1),
    PRIMARY KEY (batch_id, kind, wallet, asset)
);

-- uid is null when (venue, category, exch_ticker) is not in the symbol mirror.
-- position_amt of 0 is an open order, not a position, so it is not stored.
-- All four margin amounts are kept; v3 has no marginType.
CREATE TABLE ledger.position_snapshot (
    batch_id                 BIGINT  NOT NULL REFERENCES ledger.snapshot_batch (id),
    kind                     TEXT    NOT NULL,
    venue                    TEXT    NOT NULL CHECK (venue <> ''),
    category                 TEXT    NOT NULL CHECK (category <> ''),
    exch_ticker              TEXT    NOT NULL CHECK (exch_ticker <> ''),
    uid                      BIGINT  CHECK (uid IS NULL OR uid BETWEEN 1 AND 4294967295),
    position_side            TEXT    NOT NULL CHECK (position_side IN ('BOTH', 'LONG', 'SHORT')),
    position_amt             NUMERIC NOT NULL CHECK (position_amt <> 0),
    entry_price              NUMERIC NOT NULL,
    mark_price               NUMERIC NOT NULL,
    unrealized_profit        NUMERIC NOT NULL,
    isolated_margin          NUMERIC NOT NULL,
    initial_margin           NUMERIC NOT NULL,
    position_initial_margin  NUMERIC NOT NULL,
    isolated_wallet          NUMERIC NOT NULL,
    norm_version             INTEGER NOT NULL DEFAULT 1 CHECK (norm_version >= 1),
    PRIMARY KEY (batch_id, kind, venue, category, exch_ticker, position_side)
);

-- Hours that should have a batch and do not. The current hour is not a gap.
-- kinds is the list the worker can snapshot; accounts outside it are omitted,
-- and so are accounts that no longer have an active read credential.
CREATE FUNCTION ledger.snapshot_gaps(kinds text[], as_of timestamptz)
RETURNS TABLE (exchange_account_id bigint, bucket timestamptz)
LANGUAGE sql
STABLE
AS $$
    WITH covered AS (
        SELECT a.id,
               (date_trunc('hour', min(c.verified_at) AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') AS start_hour
        FROM pms.exchange_account a
        JOIN pms.credential c
          ON c.exchange_account_id = a.id
         AND c.purpose = 'read'
         AND c.verified_at IS NOT NULL
        WHERE a.deleted_at IS NULL
          AND EXISTS (
              SELECT 1 FROM pms.credential live
              WHERE live.exchange_account_id = a.id
                AND live.purpose = 'read'
                AND live.status = 'active'
                AND octet_length(live.ciphertext) > 0
          )
          AND EXISTS (
              SELECT 1 FROM pms.exchange_account_kind k
              WHERE k.exchange_account_id = a.id
                AND k.disabled_at IS NULL
                AND k.kind = ANY(COALESCE(kinds, '{}'::text[]))
          )
        GROUP BY a.id
    )
    SELECT covered.id, gs.bucket
    FROM covered
    CROSS JOIN LATERAL generate_series(
        covered.start_hour,
        (date_trunc('hour', as_of AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') - interval '1 hour',
        interval '1 hour'
    ) AS gs(bucket)
    WHERE NOT EXISTS (
        SELECT 1 FROM ledger.snapshot_batch b
        WHERE b.exchange_account_id = covered.id AND b.bucket = gs.bucket
    );
$$;

-- A finished successful snapshot job must not be inserted again in the same hour.
-- The stale path ends the job without a batch; without this index the rule would
-- scan job_run by dedup_key to see that.
CREATE INDEX job_run_dedup_key ON ingest.job_run (dedup_key);

CREATE SEQUENCE raw.venue_snapshot_id_seq;

CREATE TABLE raw.venue_snapshot (
    id                  BIGINT      NOT NULL DEFAULT nextval('raw.venue_snapshot_id_seq'),
    exchange_account_id BIGINT      NOT NULL REFERENCES pms.exchange_account (id),
    venue               TEXT        NOT NULL CHECK (venue <> ''),
    kind                TEXT        NOT NULL CHECK (kind <> ''),
    bucket              TIMESTAMPTZ NOT NULL,
    params              JSONB       NOT NULL,
    payload             JSONB       NOT NULL,
    fetched_at          TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (fetched_at, id),
    CHECK (bucket = (date_trunc('hour', bucket AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'))
) PARTITION BY RANGE (fetched_at);

ALTER SEQUENCE raw.venue_snapshot_id_seq OWNED BY raw.venue_snapshot.id;

CREATE TABLE raw.venue_snapshot_default PARTITION OF raw.venue_snapshot DEFAULT;

-- UPDATE and DELETE are rejected. ensure_venue_snapshot_partition sets
-- cpanel.venue_snapshot_moving for the DELETE that moves rows out of the
-- default partition, then clears it before returning.
CREATE FUNCTION raw.reject_venue_snapshot_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' AND current_setting('cpanel.venue_snapshot_moving', true) = 'on' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'raw.venue_snapshot is immutable';
END;
$$;

CREATE TRIGGER venue_snapshot_immutable
    BEFORE UPDATE OR DELETE ON raw.venue_snapshot
    FOR EACH ROW
    EXECUTE FUNCTION raw.reject_venue_snapshot_change();

-- Make the venue_snapshot partition for the UTC month of month. Rows already in
-- the default partition for that month move into it first. Returns false when
-- the partition exists. Partitions are never dropped: raw is kept forever.
CREATE FUNCTION raw.ensure_venue_snapshot_partition(month DATE) RETURNS BOOLEAN
LANGUAGE plpgsql AS $$
DECLARE
    start_month DATE        := date_trunc('month', month)::date;
    part        TEXT        := 'venue_snapshot_' || to_char(start_month, 'YYYYMM');
    lo          TIMESTAMPTZ := start_month::timestamp AT TIME ZONE 'UTC';
    hi          TIMESTAMPTZ := (start_month + INTERVAL '1 month')::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF to_regclass(format('raw.%I', part)) IS NOT NULL THEN
        RETURN false;
    END IF;
    EXECUTE format('CREATE TABLE raw.%I (LIKE raw.venue_snapshot INCLUDING DEFAULTS INCLUDING CONSTRAINTS)', part);
    EXECUTE format('ALTER TABLE raw.%I ADD CHECK (fetched_at >= %L AND fetched_at < %L)', part, lo, hi);
    PERFORM set_config('cpanel.venue_snapshot_moving', 'on', true);
    EXECUTE format('WITH moved AS (DELETE FROM raw.venue_snapshot_default WHERE fetched_at >= %L AND fetched_at < %L RETURNING *) '
                   'INSERT INTO raw.%I SELECT * FROM moved', lo, hi, part);
    PERFORM set_config('cpanel.venue_snapshot_moving', 'off', true);
    EXECUTE format('ALTER TABLE raw.venue_snapshot ATTACH PARTITION raw.%I FOR VALUES FROM (%L) TO (%L)', part, lo, hi);
    RETURN true;
END
$$;
