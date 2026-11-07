-- Schema skeleton (C2-02). The other tables arrive with later tickets.
-- meta.schema_migrations is not created here: cpanel-migrate creates it before applying this file.

CREATE SCHEMA IF NOT EXISTS pms;
CREATE SCHEMA IF NOT EXISTS mirror;
CREATE SCHEMA IF NOT EXISTS ingest;
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS ledger;
CREATE SCHEMA IF NOT EXISTS meta;

-- One row per audited action (ARCHITECTURE §4.1, §5). C2-04 writes the rows.
CREATE TABLE meta.audit (
    id         BIGSERIAL PRIMARY KEY,
    at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor      TEXT        NOT NULL,
    action     TEXT        NOT NULL,
    target     TEXT        NOT NULL,
    params     JSONB,
    result     JSONB,
    request_id TEXT        NOT NULL
);
