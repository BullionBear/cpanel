DROP TABLE IF EXISTS ledger.funding;
DROP TABLE IF EXISTS ledger.order;
DROP TABLE IF EXISTS ledger.trade;

DROP TABLE IF EXISTS raw.venue_trade;
DROP TABLE IF EXISTS raw.venue_order;
DROP TABLE IF EXISTS raw.venue_income;

DROP TABLE IF EXISTS raw.venue_trade_key;
DROP TABLE IF EXISTS raw.venue_order_key;
DROP TABLE IF EXISTS raw.venue_income_key;

DROP FUNCTION IF EXISTS raw.venue_history_key();
DROP FUNCTION IF EXISTS raw.reject_history_change();
DROP FUNCTION IF EXISTS raw.history_parent(name);

-- 0012's function body, restored so snapshot partitions still attach after this down.
CREATE OR REPLACE FUNCTION raw.ensure_venue_snapshot_partition(month DATE) RETURNS BOOLEAN
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

DROP FUNCTION IF EXISTS raw.ensure_month_partition(text, date);
