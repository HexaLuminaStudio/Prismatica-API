-- Prismatica P0-A canonical schema
-- MySQL 8.0+ / utf8mb4 / BIGINT user identity
-- migrate:up

CREATE TABLE IF NOT EXISTS users (
    id                    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    email                 VARCHAR(254) NOT NULL,
    password_hash         VARCHAR(255) NOT NULL,
    display_name          VARCHAR(64) NOT NULL DEFAULT '',
    tier                  VARCHAR(16) NOT NULL DEFAULT 'free',
    status                VARCHAR(16) NOT NULL DEFAULT 'active',
    auth_version          BIGINT NOT NULL DEFAULT 0,
    failed_login_count    INT UNSIGNED NOT NULL DEFAULT 0,
    locked_until          DATETIME(3) NULL,
    email_verified        TINYINT(1) NOT NULL DEFAULT 0,
    deleted_at            DATETIME(3) NULL,
    created_at            DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at            DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_users_email (email),
    KEY idx_users_status_tier (status, tier),
    KEY idx_users_created_at (created_at),
    CONSTRAINT chk_users_tier CHECK (tier IN ('free', 'pro', 'team')),
    CONSTRAINT chk_users_status CHECK (status IN ('active', 'paused', 'banned', 'deleted'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='终端用户账号';

CREATE TABLE IF NOT EXISTS user_devices (
    id                    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id               BIGINT UNSIGNED NOT NULL,
    device_id             VARCHAR(64) NOT NULL,
    device_name           VARCHAR(128) NOT NULL DEFAULT '',
    platform              VARCHAR(32) NOT NULL DEFAULT '',
    status                VARCHAR(16) NOT NULL DEFAULT 'active',
    first_seen_at         DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    last_seen_at          DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    revoked_at            DATETIME(3) NULL,
    created_at            DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_user_devices_user_device (user_id, device_id),
    KEY idx_user_devices_user_status (user_id, status),
    KEY idx_user_devices_last_seen (last_seen_at),
    CONSTRAINT fk_user_devices_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT chk_user_devices_status CHECK (status IN ('active', 'revoked'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='用户设备';

CREATE TABLE IF NOT EXISTS subscriptions (
    id                    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id               BIGINT UNSIGNED NOT NULL,
    plan_code             VARCHAR(32) NOT NULL,
    status                VARCHAR(16) NOT NULL DEFAULT 'active',
    started_at            DATETIME(3) NOT NULL,
    current_period_start  DATETIME(3) NOT NULL,
    current_period_end    DATETIME(3) NOT NULL,
    expires_at            DATETIME(3) NOT NULL,
    next_grant_at         DATETIME(3) NULL,
    auto_renew            TINYINT(1) NOT NULL DEFAULT 0,
    monthly_quota         INT UNSIGNED NOT NULL DEFAULT 0,
    created_at            DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at            DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    KEY idx_subscriptions_user_status (user_id, status),
    KEY idx_subscriptions_grant_due (status, next_grant_at),
    KEY idx_subscriptions_expiry (status, expires_at),
    CONSTRAINT fk_subscriptions_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
    CONSTRAINT chk_subscriptions_status CHECK (status IN ('active', 'past_due', 'canceled', 'expired')),
    CONSTRAINT chk_subscriptions_period CHECK (
        started_at <= current_period_start
        AND current_period_start < current_period_end
        AND current_period_end <= expires_at
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='用户订阅';

CREATE TABLE IF NOT EXISTS user_balance (
    user_id               BIGINT UNSIGNED NOT NULL,
    balance               BIGINT UNSIGNED NOT NULL DEFAULT 0,
    reserved              BIGINT UNSIGNED NOT NULL DEFAULT 0,
    available             BIGINT AS (balance - reserved) STORED,
    lifetime_grant        BIGINT UNSIGNED NOT NULL DEFAULT 0,
    lifetime_consumed     BIGINT UNSIGNED NOT NULL DEFAULT 0,
    version               INT UNSIGNED NOT NULL DEFAULT 0,
    updated_at            DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (user_id),
    CONSTRAINT fk_user_balance_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT chk_user_balance_reserved CHECK (reserved <= balance)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='用户积分余额';

CREATE TABLE IF NOT EXISTS admin_users (
    user_id               CHAR(36) NOT NULL,
    username              VARCHAR(64) NOT NULL,
    password_hash         VARCHAR(255) NOT NULL,
    role                  VARCHAR(32) NOT NULL DEFAULT 'admin',
    status                VARCHAR(16) NOT NULL DEFAULT 'active',
    last_login_at         DATETIME(3) NULL,
    failed_attempts       INT UNSIGNED NOT NULL DEFAULT 0,
    deleted_at            DATETIME(3) NULL,
    pwd_reset_at          DATETIME(3) NULL,
    created_at            DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at            DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (user_id),
    UNIQUE KEY uk_admin_users_username (username),
    KEY idx_admin_users_status (status),
    KEY idx_admin_users_deleted_at (deleted_at),
    CONSTRAINT chk_admin_users_role CHECK (role IN ('owner', 'admin')),
    CONSTRAINT chk_admin_users_status CHECK (status IN ('active', 'locked'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='运营管理员账号';

CREATE TABLE IF NOT EXISTS license_codes (
    id                    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    code_hash             CHAR(64) NOT NULL,
    code_kind             VARCHAR(8) NOT NULL,
    status                VARCHAR(16) NOT NULL DEFAULT 'active',
    plan_code             VARCHAR(32) NULL,
    period_months         SMALLINT UNSIGNED NULL,
    trial_days            SMALLINT UNSIGNED NULL,
    monthly_quota         INT UNSIGNED NULL,
    amount                BIGINT UNSIGNED NULL,
    max_uses              INT UNSIGNED NOT NULL DEFAULT 1,
    used_count            INT UNSIGNED NOT NULL DEFAULT 0,
    issued_by             CHAR(36) NULL,
    note                  VARCHAR(255) NOT NULL DEFAULT '',
    issued_at             DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    expires_at            DATETIME(3) NULL,
    revoked_at            DATETIME(3) NULL,
    created_at            DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_license_codes_hash (code_hash),
    KEY idx_license_codes_kind_status (code_kind, status),
    KEY idx_license_codes_expires_at (status, expires_at),
    CONSTRAINT fk_license_codes_admin FOREIGN KEY (issued_by) REFERENCES admin_users(user_id) ON DELETE SET NULL,
    CONSTRAINT chk_license_codes_kind CHECK (code_kind IN ('INV', 'RCH', 'TRY')),
    CONSTRAINT chk_license_codes_status CHECK (status IN ('active', 'exhausted', 'revoked', 'expired')),
    CONSTRAINT chk_license_codes_usage CHECK (max_uses > 0 AND used_count <= max_uses),
    CONSTRAINT chk_license_codes_payload CHECK (
        (code_kind = 'RCH' AND amount IS NOT NULL AND amount > 0)
        OR (code_kind = 'INV' AND plan_code IS NOT NULL AND period_months IS NOT NULL AND period_months > 0 AND monthly_quota IS NOT NULL)
        OR (code_kind = 'TRY' AND trial_days IS NOT NULL AND trial_days > 0 AND monthly_quota IS NOT NULL)
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='兑换码';

CREATE TABLE IF NOT EXISTS code_redemptions (
    id                    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    code_id               BIGINT UNSIGNED NOT NULL,
    user_id               BIGINT UNSIGNED NOT NULL,
    subscription_id       BIGINT UNSIGNED NULL,
    amount_granted        BIGINT UNSIGNED NOT NULL DEFAULT 0,
    client_ip             VARCHAR(64) NULL,
    redeemed_at           DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_code_redemptions_code_user (code_id, user_id),
    KEY idx_code_redemptions_user_time (user_id, redeemed_at),
    CONSTRAINT fk_code_redemptions_code FOREIGN KEY (code_id) REFERENCES license_codes(id) ON DELETE RESTRICT,
    CONSTRAINT fk_code_redemptions_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
    CONSTRAINT fk_code_redemptions_subscription FOREIGN KEY (subscription_id) REFERENCES subscriptions(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='兑换记录';

CREATE TABLE IF NOT EXISTS bills (
    id                    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    bill_id               CHAR(36) NOT NULL,
    user_id               BIGINT UNSIGNED NOT NULL,
    feature               VARCHAR(64) NOT NULL,
    estimated_cost        BIGINT UNSIGNED NOT NULL,
    actual_cost           BIGINT UNSIGNED NULL,
    status                VARCHAR(16) NOT NULL DEFAULT 'pending',
    idempotency_key       VARCHAR(64) NOT NULL,
    request_hash          CHAR(64) NOT NULL,
    description           VARCHAR(255) NOT NULL DEFAULT '',
    preauth_expires_at    DATETIME(3) NOT NULL,
    settled_at            DATETIME(3) NULL,
    refunded_at           DATETIME(3) NULL,
    created_at            DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at            DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_bills_bill_id (bill_id),
    UNIQUE KEY uk_bills_user_idempotency (user_id, idempotency_key),
    KEY idx_bills_user_status_time (user_id, status, created_at),
    KEY idx_bills_pending_expiry (status, preauth_expires_at),
    CONSTRAINT fk_bills_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
    CONSTRAINT chk_bills_status CHECK (status IN ('pending', 'settled', 'refunded', 'expired')),
    CONSTRAINT chk_bills_actual_cost CHECK (actual_cost IS NULL OR actual_cost <= estimated_cost)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='计费账单';

CREATE TABLE IF NOT EXISTS balance_ledger (
    id                    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id               BIGINT UNSIGNED NOT NULL,
    entry_type            VARCHAR(16) NOT NULL,
    amount                BIGINT NOT NULL,
    balance_delta         BIGINT NOT NULL DEFAULT 0,
    reserved_delta        BIGINT NOT NULL DEFAULT 0,
    balance_after         BIGINT UNSIGNED NOT NULL,
    reserved_after        BIGINT UNSIGNED NOT NULL,
    source                VARCHAR(32) NOT NULL,
    ref_type              VARCHAR(32) NULL,
    ref_id                VARCHAR(64) NULL,
    note                  VARCHAR(255) NOT NULL DEFAULT '',
    created_at            DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    KEY idx_balance_ledger_user_time (user_id, created_at),
    KEY idx_balance_ledger_source_ref (source, ref_type, ref_id),
    CONSTRAINT fk_balance_ledger_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
    CONSTRAINT chk_balance_ledger_type CHECK (entry_type IN ('grant', 'consume', 'refund', 'reserve', 'unreserve', 'adjust')),
    CONSTRAINT chk_balance_ledger_after CHECK (reserved_after <= balance_after)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='余额不可变账本';

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id                    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id               BIGINT UNSIGNED NOT NULL,
    token_hash            CHAR(64) NOT NULL,
    expires_at            DATETIME(3) NOT NULL,
    used_at               DATETIME(3) NULL,
    created_at            DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_password_reset_token_hash (token_hash),
    KEY idx_password_reset_user_expiry (user_id, expires_at),
    CONSTRAINT fk_password_reset_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='密码重置令牌';

CREATE TABLE IF NOT EXISTS refresh_tokens (
    id                    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    jti                   CHAR(36) NOT NULL,
    token_hash            CHAR(64) NOT NULL,
    user_id               BIGINT UNSIGNED NOT NULL,
    device_id             BIGINT UNSIGNED NOT NULL,
    expires_at            DATETIME(3) NOT NULL,
    revoked_at            DATETIME(3) NULL,
    revoke_reason         VARCHAR(32) NULL,
    replaced_by_jti       CHAR(36) NULL,
    created_at            DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_refresh_tokens_jti (jti),
    UNIQUE KEY uk_refresh_tokens_hash (token_hash),
    KEY idx_refresh_tokens_user_expiry (user_id, expires_at),
    KEY idx_refresh_tokens_device_active (device_id, revoked_at, expires_at),
    CONSTRAINT fk_refresh_tokens_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_refresh_tokens_device FOREIGN KEY (device_id) REFERENCES user_devices(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='可轮换 Refresh Token';

CREATE TABLE IF NOT EXISTS revoked_tokens (
    jti                   CHAR(36) NOT NULL,
    user_id               BIGINT UNSIGNED NOT NULL,
    token_type            VARCHAR(16) NOT NULL,
    reason                VARCHAR(32) NOT NULL DEFAULT 'logout',
    expires_at            DATETIME(3) NOT NULL,
    revoked_at            DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (jti),
    KEY idx_revoked_tokens_expiry (expires_at),
    KEY idx_revoked_tokens_user (user_id, revoked_at),
    CONSTRAINT fk_revoked_tokens_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT chk_revoked_tokens_type CHECK (token_type IN ('access', 'refresh'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='JWT 吊销列表';

CREATE TABLE IF NOT EXISTS idempotency_keys (
    id                    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id               BIGINT UNSIGNED NOT NULL,
    operation             VARCHAR(32) NOT NULL,
    idempotency_key       VARCHAR(64) NOT NULL,
    request_hash          CHAR(64) NOT NULL,
    response_status       SMALLINT UNSIGNED NULL,
    response_body         JSON NULL,
    resource_type         VARCHAR(32) NULL,
    resource_id           VARCHAR(64) NULL,
    expires_at            DATETIME(3) NOT NULL,
    created_at            DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at            DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_idempotency_scope (user_id, operation, idempotency_key),
    KEY idx_idempotency_expiry (expires_at),
    CONSTRAINT fk_idempotency_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT chk_idempotency_response_status CHECK (response_status IS NULL OR response_status BETWEEN 100 AND 599)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='24 小时幂等响应缓存';

CREATE TABLE IF NOT EXISTS audit_logs (
    audit_id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    actor_type            VARCHAR(16) NOT NULL DEFAULT 'system',
    actor                 VARCHAR(64) NOT NULL,
    action                VARCHAR(64) NOT NULL,
    target_type           VARCHAR(32) NULL,
    target_id             VARCHAR(64) NULL,
    target_user           VARCHAR(64) NULL,
    request_id            VARCHAR(64) NULL,
    before_data           JSON NULL,
    after_data            JSON NULL,
    details               JSON NULL,
    ip                    VARCHAR(64) NULL,
    created_at            DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (audit_id),
    KEY idx_audit_logs_actor_time (actor, created_at),
    KEY idx_audit_logs_action_time (action, created_at),
    KEY idx_audit_logs_target_time (target_type, target_id, created_at),
    KEY idx_audit_logs_target_user_time (target_user, created_at),
    KEY idx_audit_logs_request_id (request_id),
    CONSTRAINT chk_audit_logs_actor_type CHECK (actor_type IN ('user', 'admin', 'system'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='安全与业务审计日志';

-- migrate:down

DROP TABLE IF EXISTS audit_logs;
DROP TABLE IF EXISTS idempotency_keys;
DROP TABLE IF EXISTS revoked_tokens;
DROP TABLE IF EXISTS refresh_tokens;
DROP TABLE IF EXISTS password_reset_tokens;
DROP TABLE IF EXISTS balance_ledger;
DROP TABLE IF EXISTS bills;
DROP TABLE IF EXISTS code_redemptions;
DROP TABLE IF EXISTS license_codes;
DROP TABLE IF EXISTS admin_users;
DROP TABLE IF EXISTS user_balance;
DROP TABLE IF EXISTS subscriptions;
DROP TABLE IF EXISTS user_devices;
DROP TABLE IF EXISTS users;
