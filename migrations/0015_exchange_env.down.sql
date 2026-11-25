ALTER TABLE pms.credential
    DROP CONSTRAINT credential_key_type_check,
    DROP CONSTRAINT credential_exchange_env_check,
    DROP COLUMN key_type,
    DROP COLUMN exchange_env;
