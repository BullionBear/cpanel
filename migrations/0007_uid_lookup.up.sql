-- Scope UID lookup (C4-05, #78). Decision C4-D012.
--
-- mirror.uid_lookup remembers a scope UID that SYM did not put into mirror.symbol.
-- NotFound and a universal ticker that does not parse are both rows here. A later
-- successful GetByUid deletes the row. The table is not a foreign key to mirror.scope:
-- the scope row can disappear from a later read without this history being the reason.

CREATE TABLE mirror.uid_lookup (
    uid            BIGINT PRIMARY KEY CHECK (uid BETWEEN 1 AND 4294967295),
    first_tried_at TIMESTAMPTZ NOT NULL,
    last_tried_at  TIMESTAMPTZ NOT NULL,
    attempts       BIGINT      NOT NULL CHECK (attempts >= 1),
    last_result    TEXT        NOT NULL CHECK (last_result IN ('not_found', 'unparsable'))
);
