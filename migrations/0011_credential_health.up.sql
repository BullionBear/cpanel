-- Credential health (C5-04, #89).
--
-- A deleted credential keeps its row, status and last four characters. The
-- ciphertext is cleared, and only a disabled row may hold an empty ciphertext.
-- next_probe_at is when reconcile may insert the next credential.probe.

DO $$
DECLARE
    name name;
BEGIN
    SELECT c.conname INTO name
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace ns ON ns.oid = t.relnamespace
    WHERE ns.nspname = 'pms' AND t.relname = 'credential' AND c.contype = 'c'
      AND pg_get_constraintdef(c.oid) LIKE '%octet_length(ciphertext)%';
    IF name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE pms.credential DROP CONSTRAINT %I', name);
    END IF;
END $$;

ALTER TABLE pms.credential
    ADD CONSTRAINT credential_ciphertext_present
    CHECK (octet_length(ciphertext) > 0 OR status = 'disabled');

ALTER TABLE pms.credential
    ADD COLUMN next_probe_at TIMESTAMPTZ;
