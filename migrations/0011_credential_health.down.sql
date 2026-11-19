ALTER TABLE pms.credential DROP COLUMN next_probe_at;

ALTER TABLE pms.credential DROP CONSTRAINT credential_ciphertext_present;

ALTER TABLE pms.credential
    ADD CONSTRAINT credential_ciphertext_check
    CHECK (octet_length(ciphertext) > 0);
