-- Prismatica 资源保护设备密钥迁移
-- MySQL 8.0+
-- migrate:up

ALTER TABLE user_devices
    ADD COLUMN resource_encryption_public_key VARCHAR(64) NULL AFTER revoked_at,
    ADD COLUMN resource_signing_public_key VARCHAR(64) NULL AFTER resource_encryption_public_key,
    ADD COLUMN resource_key_updated_at DATETIME(3) NULL AFTER resource_signing_public_key;

-- migrate:down

ALTER TABLE user_devices
    DROP COLUMN resource_key_updated_at,
    DROP COLUMN resource_signing_public_key,
    DROP COLUMN resource_encryption_public_key;
