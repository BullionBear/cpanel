-- Exchange environment and key type (C7-09, #103).
--
-- exchange_env is the deployment that created the credential. A worker of the
-- other environment refuses it. key_type is hmac or ed25519. Existing rows are
-- prod and hmac, which is what a ciphertext without key_type already means.

ALTER TABLE pms.credential
    ADD COLUMN exchange_env text NOT NULL DEFAULT 'prod',
    ADD COLUMN key_type text NOT NULL DEFAULT 'hmac';

ALTER TABLE pms.credential
    ADD CONSTRAINT credential_exchange_env_check CHECK (exchange_env IN ('prod', 'testnet')),
    ADD CONSTRAINT credential_key_type_check CHECK (key_type IN ('hmac', 'ed25519'));
