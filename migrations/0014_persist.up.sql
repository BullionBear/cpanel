-- Persist list and stream source names (C7-02, #102). Decisions C5-D007, C7-D001.
--
-- ingest.persist_symbol is the list of (account, venue, uid) whose history is kept.
-- source is only mftiko or manual. Rows are not deleted, and since only moves earlier.
-- A symbol is not stored: uid is mirror.symbol's primary key.
--
-- ingest.stream.source loses discovered. scope is renamed mftiko. Account-level
-- streams (funding) keep system, which is not a third list source.

-- Drop the check before rewriting. scope is not a member of the new set, and
-- mftiko is not a member of the old one, so either order of update-then-check fails.
ALTER TABLE ingest.stream DROP CONSTRAINT stream_source_check;

UPDATE ingest.stream SET source = 'mftiko' WHERE source = 'scope';

-- Nothing writes discovered. There is no list source to map it to.
DELETE FROM ingest.stream WHERE source = 'discovered';

ALTER TABLE ingest.stream ADD CONSTRAINT stream_source_check
    CHECK (source IN ('mftiko', 'manual', 'system'));

CREATE TABLE ingest.persist_symbol (
    exchange_account_id BIGINT      NOT NULL REFERENCES pms.exchange_account (id),
    venue               TEXT        NOT NULL CHECK (venue <> ''),
    uid                 BIGINT      NOT NULL CHECK (uid BETWEEN 1 AND 4294967295),
    source              TEXT        NOT NULL CHECK (source IN ('mftiko', 'manual')),
    since               TIMESTAMPTZ NOT NULL,
    first_seen_at       TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (exchange_account_id, venue, uid, source)
);
