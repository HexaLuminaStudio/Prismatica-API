-- migrate:up
ALTER TABLE users ADD COLUMN auth_version BIGINT NOT NULL DEFAULT 0 AFTER status;

-- migrate:down
ALTER TABLE users DROP COLUMN auth_version;
