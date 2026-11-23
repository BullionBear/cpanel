-- Reverse 0014. mftiko streams become scope again so the old check accepts them.
-- discovered rows deleted on the way up are not recreated; the old check allows the value.

DROP TABLE ingest.persist_symbol;

ALTER TABLE ingest.stream DROP CONSTRAINT stream_source_check;

UPDATE ingest.stream SET source = 'scope' WHERE source = 'mftiko';

ALTER TABLE ingest.stream ADD CONSTRAINT stream_source_check
    CHECK (source IN ('scope', 'discovered', 'manual', 'system'));
