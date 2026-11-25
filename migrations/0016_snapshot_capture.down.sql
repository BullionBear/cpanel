-- The 0012 key holds category, so a position the mirror did not know cannot stay.

CREATE OR REPLACE FUNCTION ledger.snapshot_gaps(kinds text[], as_of timestamptz)
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

DROP INDEX mirror.symbol_venue_ticker;

DELETE FROM ledger.position_snapshot WHERE category IS NULL;

ALTER TABLE ledger.position_snapshot
    DROP CONSTRAINT position_snapshot_pkey,
    ALTER COLUMN category SET NOT NULL,
    ADD PRIMARY KEY (batch_id, kind, venue, category, exch_ticker, position_side);
