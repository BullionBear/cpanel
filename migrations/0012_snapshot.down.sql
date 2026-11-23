DROP INDEX IF EXISTS ingest.job_run_dedup_key;

DROP FUNCTION IF EXISTS ledger.snapshot_gaps(text[], timestamptz);
DROP TABLE IF EXISTS ledger.position_snapshot;
DROP TABLE IF EXISTS ledger.balance_snapshot;
DROP TABLE IF EXISTS ledger.snapshot_batch_kind;
DROP TABLE IF EXISTS ledger.snapshot_batch;

DROP FUNCTION IF EXISTS raw.ensure_venue_snapshot_partition(date);
DROP TABLE IF EXISTS raw.venue_snapshot;
DROP FUNCTION IF EXISTS raw.reject_venue_snapshot_change();
