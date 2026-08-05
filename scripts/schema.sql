-- =====================================================================
-- Prismatica 后端 schema(PRD v2 §10)
-- 数据库:MySQL 8.0+ / 字符集 utf8mb4 / 排序规则 utf8mb4_unicode_ci
-- 所有金额 BIGINT;时间 TIMESTAMP;幂等字段 CHAR(36) UUID
--
-- 使用方式(数据库已存在):
--     直接执行 CREATE TABLE 部分(顶部 CREATE DATABASE/USE 已注释)
--     或保持顶部 USE <db>; 仅当库名匹配时执行
-- =====================================================================

-- 顶部 DDL 由调用方按目标库注入;此处不再硬编码 CREATE DATABASE/USE
-- (开发环境若想一键建库,可解开下面两行)
-- CREATE DATABASE IF NOT EXISTS prismatica DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci;
-- USE prismatica;

-- ---------------------------------------------------------------------
-- 用户账户
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_accounts (
    user_id        CHAR(36) PRIMARY KEY,
    display_name   VARCHAR(64) NOT NULL,
    tier           VARCHAR(16) NOT NULL DEFAULT 'beta',
    status         VARCHAR(16) NOT NULL DEFAULT 'active',  -- active/suspended/expired
    activated_at   TIMESTAMP NOT NULL,
    expire_at      TIMESTAMP NULL,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_user_accounts_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户账户';

-- ---------------------------------------------------------------------
-- 设备(多设备登录)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_devices (
    device_id      CHAR(36) PRIMARY KEY,
    user_id        CHAR(36) NOT NULL,
    device_name    VARCHAR(128) NOT NULL DEFAULT '',
    platform       VARCHAR(32) NOT NULL DEFAULT '',
    first_seen_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_user_devices_user FOREIGN KEY (user_id) REFERENCES user_accounts(user_id),
    KEY idx_user_devices_user (user_id, last_seen_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户设备';

-- ---------------------------------------------------------------------
-- 用户余额(1:1 行锁粒度)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_balances (
    user_id         CHAR(36) PRIMARY KEY,
    balance         BIGINT NOT NULL DEFAULT 0,
    frozen_balance  BIGINT NOT NULL DEFAULT 0,
    total_spent     BIGINT NOT NULL DEFAULT 0,
    total_recharged BIGINT NOT NULL DEFAULT 0,
    version         INT NOT NULL DEFAULT 0,  -- 乐观锁
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_user_balances_user FOREIGN KEY (user_id) REFERENCES user_accounts(user_id),
    CONSTRAINT chk_user_balances_balance CHECK (balance >= 0),
    CONSTRAINT chk_user_balances_frozen CHECK (frozen_balance >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户余额';

-- ---------------------------------------------------------------------
-- 凭证码全局幂等(替代本地 SQLite 的 recharge_codes)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS license_codes_seen (
    code_hash             CHAR(64) PRIMARY KEY,
    code_kind             ENUM('invite','trial','recharge') NOT NULL,
    issued_at             TIMESTAMP NULL,
    consumed_at           TIMESTAMP NULL,
    consumed_by_user_id   CHAR(36) NULL,
    consume_ip            VARCHAR(64) NULL,
    recharge_user_id      CHAR(36) NULL,
    recharge_amount       INT NULL,
    expire_at             TIMESTAMP NULL,
    KEY idx_codes_seen_user (consumed_by_user_id, consumed_at),
    KEY idx_codes_seen_kind (code_kind, expire_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='凭证码幂等表';

-- ---------------------------------------------------------------------
-- 账单流水
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bills (
    bill_id              CHAR(36) PRIMARY KEY,
    user_id              CHAR(36) NOT NULL,
    action_type          VARCHAR(32) NOT NULL,
    action_display_name  VARCHAR(64) NOT NULL DEFAULT '',
    estimated_cost       INT NOT NULL DEFAULT 0,
    real_cost            INT NOT NULL DEFAULT 0,
    resource_used        BIGINT NOT NULL DEFAULT 0,
    balance_before       BIGINT NOT NULL DEFAULT 0,
    balance_after        BIGINT NOT NULL DEFAULT 0,
    status               VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending/settled/refunded
    task_id              CHAR(36) NOT NULL DEFAULT '',
    description          VARCHAR(256) NOT NULL DEFAULT '',
    idempotency_key      CHAR(36) NULL,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    settled_at           TIMESTAMP NULL,
    CONSTRAINT fk_bills_user FOREIGN KEY (user_id) REFERENCES user_accounts(user_id),
    UNIQUE KEY uk_bills_idem (idempotency_key),
    KEY idx_bills_user_status (user_id, status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='账单流水';

-- ---------------------------------------------------------------------
-- 充值/赠送记录
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS recharge_records (
    record_id      CHAR(36) PRIMARY KEY,
    user_id        CHAR(36) NOT NULL,
    amount         INT NOT NULL,
    source         VARCHAR(32) NOT NULL,  -- activation_grant / recharge_code / manual_gift / admin_grant
    code_hash      CHAR(64) NULL,
    operator_note  VARCHAR(256) NOT NULL DEFAULT '',
    balance_before BIGINT NOT NULL DEFAULT 0,
    balance_after  BIGINT NOT NULL DEFAULT 0,
    expire_at      TIMESTAMP NULL,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_recharge_records_user FOREIGN KEY (user_id) REFERENCES user_accounts(user_id),
    CONSTRAINT chk_recharge_amount CHECK (amount > 0),
    KEY idx_recharge_records_user (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='充值/赠送流水';

-- ---------------------------------------------------------------------
-- Refresh Token(允许主动 revoke)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_id     CHAR(36) PRIMARY KEY,
    user_id      CHAR(36) NOT NULL,
    device_id    CHAR(36) NOT NULL,
    expires_at   TIMESTAMP NOT NULL,
    revoked_at   TIMESTAMP NULL,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_refresh_user FOREIGN KEY (user_id) REFERENCES user_accounts(user_id),
    KEY idx_refresh_user (user_id, expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Refresh Token';

-- ---------------------------------------------------------------------
-- 审计日志(所有 admin 行为)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_logs (
    audit_id     BIGINT AUTO_INCREMENT PRIMARY KEY,
    actor        VARCHAR(64) NOT NULL,   -- admin token id or system
    action       VARCHAR(64) NOT NULL,
    target_user  CHAR(36) NULL,
    details      JSON NULL,
    ip           VARCHAR(64) NULL,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    KEY idx_audit_actor_time (actor, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='审计日志';