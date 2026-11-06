-- Drops every schema from 0001_schema, including meta.schema_migrations.
-- cpanel-migrate recreates the version table on the next up.

DROP SCHEMA IF EXISTS ledger, raw, ingest, mirror, pms, meta CASCADE;
