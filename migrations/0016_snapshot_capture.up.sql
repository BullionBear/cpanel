-- BinanceUM snapshot capture (C6-03, #110). Decisions C6-D007, C6-D009.
--
-- positionRisk sends a symbol and no category. The worker finds the category and
-- the uid by (venue, exch_ticker) in mirror.symbol; a ticker the mirror does not
-- have keeps both null. Category leaves the primary key, so a row the exchange
-- sent once is stored once whatever the mirror says.
--
-- A gap starts at the account's first batch. Hours before the worker first
-- snapshotted the account are not gaps.

ALTER TABLE ledger.position_snapshot
    DROP CONSTRAINT position_snapshot_pkey,
    ALTER COLUMN category DROP NOT NULL,
    ADD PRIMARY KEY (batch_id, kind, venue, exch_ticker, position_side);

CREATE INDEX symbol_venue_ticker ON mirror.symbol (venue, exch_ticker);

CREATE OR REPLACE FUNCTION ledger.snapshot_gaps(kinds text[], as_of timestamptz)
RETURNS TABLE (exchange_account_id bigint, bucket timestamptz)
LANGUAGE sql
STABLE
AS $$
    WITH covered AS (
        SELECT a.id, min(b.bucket) AS start_hour
        FROM pms.exchange_account a
        JOIN ledger.snapshot_batch b ON b.exchange_account_id = a.id
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
