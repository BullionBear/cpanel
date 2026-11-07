-- Job system schema (C3-01, #64). Decisions C3-D001–D004 in docs/DECISIONS.md.
--
-- ingest.job holds only unfinished jobs and is not partitioned, so its UNIQUE (dedup_key) holds
-- across days (C3-D001, C3-D002). A finished job leaves ingest.job and lands in ingest.job_run,
-- partitioned by day of finished_at, in the same transaction. Correctness does not rest on the
-- lease: a page commits its rows and advances ingest.stream.cursor by compare-and-swap on
-- cursor_version (C3-D004).

-- Error classes a job can end with (C3-04). internal is a bug or a database error on our side.
CREATE DOMAIN ingest.error_class AS TEXT
    CHECK (VALUE IN ('auth', 'rate_limited', 'ip_banned', 'unavailable', 'bad_request', 'not_found', 'internal'));

-- One stream is a schedule, a cursor and a health record at once (ARCHITECTURE §4.3, §4.4).
CREATE TABLE ingest.stream (
    id                   BIGSERIAL PRIMARY KEY,
    kind                 TEXT        NOT NULL,
    lease_key            TEXT        NOT NULL,
    key                  TEXT        NOT NULL,
    source               TEXT        NOT NULL CHECK (source IN ('scope', 'discovered', 'manual', 'system')),
    tier                 TEXT        NOT NULL DEFAULT 'hot' CHECK (tier IN ('hot', 'cold')),
    period               INTERVAL    NOT NULL CHECK (period > INTERVAL '0'),
    cursor               JSONB,
    cursor_version       BIGINT      NOT NULL DEFAULT 0,
    next_due_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    paused_reason        TEXT,
    paused_at            TIMESTAMPTZ,
    last_ok_at           TIMESTAMPTZ,
    last_error_at        TIMESTAMPTZ,
    last_error_class     ingest.error_class,
    last_error           TEXT,
    consecutive_failures INTEGER     NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (kind, lease_key, key),
    CHECK ((paused_reason IS NULL) = (paused_at IS NULL))
);

CREATE INDEX stream_due ON ingest.stream (next_due_at) WHERE paused_reason IS NULL;
CREATE INDEX stream_lease_key ON ingest.stream (lease_key);

-- Singletons that run on an interval: mftiko.sync, sym.sync, maint.partitions.
CREATE TABLE ingest.periodic (
    name        TEXT PRIMARY KEY,
    kind        TEXT        NOT NULL,
    lease_key   TEXT        NOT NULL,
    period      INTERVAL    NOT NULL CHECK (period > INTERVAL '0'),
    params      JSONB       NOT NULL DEFAULT '{}',
    enabled     BOOLEAN     NOT NULL DEFAULT true,
    next_due_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Unfinished jobs only. dedup_key is stream:<id>, periodic:<name> or bucket:<kind>:<lease_key>:<time>.
CREATE TABLE ingest.job (
    id            BIGSERIAL PRIMARY KEY,
    kind          TEXT        NOT NULL,
    dedup_key     TEXT        NOT NULL UNIQUE,
    lease_key     TEXT        NOT NULL,
    stream_id     BIGINT      REFERENCES ingest.stream (id) ON DELETE CASCADE,
    periodic_name TEXT        REFERENCES ingest.periodic (name) ON DELETE CASCADE,
    priority      SMALLINT    NOT NULL DEFAULT 100,
    not_before    TIMESTAMPTZ NOT NULL DEFAULT now(),
    status        TEXT        NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running')),
    attempt       INTEGER     NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    params        JSONB       NOT NULL DEFAULT '{}',
    enqueued_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at    TIMESTAMPTZ,
    worker        TEXT,
    lease_token   BIGINT,
    CHECK (stream_id IS NULL OR periodic_name IS NULL),
    CHECK ((status = 'running') = (started_at IS NOT NULL AND worker IS NOT NULL AND lease_token IS NOT NULL))
);

CREATE INDEX job_pending ON ingest.job (priority, not_before, id) WHERE status = 'pending';
CREATE INDEX job_lease_key ON ingest.job (lease_key);

-- Finished jobs, one partition per UTC day of finished_at. Daily partitions are made and dropped
-- by ingest.ensure_job_run_partition and ingest.expire_job_runs (called by maint.partitions,
-- C3-03). The default partition catches rows when maintenance lags; ensure_job_run_partition
-- moves them out before attaching, since a range cannot be attached while the default holds rows in it.
CREATE TABLE ingest.job_run (
    id            BIGINT             NOT NULL,
    kind          TEXT               NOT NULL,
    dedup_key     TEXT               NOT NULL,
    lease_key     TEXT               NOT NULL,
    stream_id     BIGINT,
    periodic_name TEXT,
    priority      SMALLINT           NOT NULL,
    attempt       INTEGER            NOT NULL,
    params        JSONB              NOT NULL,
    enqueued_at   TIMESTAMPTZ        NOT NULL,
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ        NOT NULL,
    worker        TEXT,
    status        TEXT               NOT NULL CHECK (status IN ('succeeded', 'failed', 'cancelled')),
    requests      INTEGER            NOT NULL DEFAULT 0 CHECK (requests >= 0),
    items         INTEGER            NOT NULL DEFAULT 0 CHECK (items >= 0),
    more          BOOLEAN            NOT NULL DEFAULT false,
    error_class   ingest.error_class,
    error         TEXT,
    PRIMARY KEY (finished_at, id),
    CHECK ((status = 'failed') = (error_class IS NOT NULL))
) PARTITION BY RANGE (finished_at);

CREATE TABLE ingest.job_run_default PARTITION OF ingest.job_run DEFAULT;

CREATE INDEX job_run_stream ON ingest.job_run (stream_id, finished_at DESC) WHERE stream_id IS NOT NULL;
CREATE INDEX job_run_kind ON ingest.job_run (kind, finished_at DESC);
CREATE INDEX job_run_failed ON ingest.job_run (error_class, finished_at DESC) WHERE status = 'failed';

-- Failed runs outlive their job_run partition: expire_job_runs copies them here before dropping it.
-- Kept a year (maint.partitions deletes older rows).
CREATE TABLE ingest.job_failure (
    LIKE ingest.job_run INCLUDING DEFAULTS INCLUDING CONSTRAINTS,
    PRIMARY KEY (finished_at, id)
);

CREATE INDEX job_failure_stream ON ingest.job_failure (stream_id, finished_at DESC) WHERE stream_id IS NOT NULL;

-- Who runs the jobs of one lease_key (account:<id>, global:mftiko, …) and until when (C3-D003).
-- token goes up by one on every acquisition.
CREATE TABLE ingest.lease (
    key         TEXT PRIMARY KEY,
    owner       TEXT        NOT NULL,
    token       BIGINT      NOT NULL DEFAULT 1 CHECK (token > 0),
    acquired_at TIMESTAMPTZ NOT NULL,
    until       TIMESTAMPTZ NOT NULL
);

-- A stop on outbound work shared by every worker behind one egress, e.g. after a Binance 418 (C3-04).
CREATE TABLE ingest.pause (
    scope  TEXT PRIMARY KEY,
    until  TIMESTAMPTZ NOT NULL,
    reason TEXT        NOT NULL,
    set_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Make the job_run partition for UTC day d. Rows already in the default partition for that day
-- move into it first. Returns false when the partition exists.
CREATE FUNCTION ingest.ensure_job_run_partition(d DATE) RETURNS BOOLEAN
LANGUAGE plpgsql AS $$
DECLARE
    part TEXT        := 'job_run_' || to_char(d, 'YYYYMMDD');
    lo   TIMESTAMPTZ := d::timestamp AT TIME ZONE 'UTC';
    hi   TIMESTAMPTZ := (d + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF to_regclass(format('ingest.%I', part)) IS NOT NULL THEN
        RETURN false;
    END IF;
    EXECUTE format('CREATE TABLE ingest.%I (LIKE ingest.job_run INCLUDING DEFAULTS INCLUDING CONSTRAINTS)', part);
    EXECUTE format('ALTER TABLE ingest.%I ADD CHECK (finished_at >= %L AND finished_at < %L)', part, lo, hi);
    EXECUTE format('WITH moved AS (DELETE FROM ingest.job_run_default WHERE finished_at >= %L AND finished_at < %L RETURNING *) '
                   'INSERT INTO ingest.%I SELECT * FROM moved', lo, hi, part);
    EXECUTE format('ALTER TABLE ingest.job_run ATTACH PARTITION ingest.%I FOR VALUES FROM (%L) TO (%L)', part, lo, hi);
    RETURN true;
END
$$;

-- Drop the daily partitions for days before keep_from, and the default partition's rows before it.
-- Failed rows are copied into ingest.job_failure first. Returns the number of partitions dropped.
CREATE FUNCTION ingest.expire_job_runs(keep_from DATE) RETURNS INTEGER
LANGUAGE plpgsql AS $$
DECLARE
    cutoff  TIMESTAMPTZ := keep_from::timestamp AT TIME ZONE 'UTC';
    part    TEXT;
    dropped INTEGER     := 0;
BEGIN
    FOR part IN
        SELECT c.relname
        FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        WHERE i.inhparent = 'ingest.job_run'::regclass
          AND c.relname ~ '^job_run_[0-9]{8}$'
          AND to_date(substr(c.relname, 9), 'YYYYMMDD') < keep_from
        ORDER BY c.relname
    LOOP
        EXECUTE format('INSERT INTO ingest.job_failure SELECT * FROM ingest.%I WHERE status = ''failed'' ON CONFLICT DO NOTHING', part);
        EXECUTE format('DROP TABLE ingest.%I', part);
        dropped := dropped + 1;
    END LOOP;
    INSERT INTO ingest.job_failure
        SELECT * FROM ingest.job_run_default WHERE finished_at < cutoff AND status = 'failed'
        ON CONFLICT DO NOTHING;
    DELETE FROM ingest.job_run_default WHERE finished_at < cutoff;
    RETURN dropped;
END
$$;
