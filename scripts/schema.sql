-- =====================================================================
-- Prismatica 后端 schema(2026-08-06 重构,方案 B)
-- 数据库:MySQL 8.0+ / 字符集 utf8mb4 / 排序规则 utf8mb4_unicode_ci
-- 所有金额 BIGINT;时间 TIMESTAMP;幂等字段 CHAR(36) UUID
--
-- 本轮整体重设计要点:
--   1. 统一命名:snake_case 字段 + 表名去掉前缀复数化(user_accounts → user_accounts 保持)
--   2. 凭证持久化:新增 license_codes 表(issued 立即落库,consume 时标记),
--      license_codes_seen 表仅作幂等 hash 索引,与 license_codes 形成 1:1
--   3. admin_users 字段补齐(role 权限矩阵 / status 细化)
--   4. 所有 admin 行为都进 audit_logs,字段统一
--   5. 时间字段统一 timestamp_ms 不强求(用 DATETIME(3) 毫秒精度即可)
--
-- 使用方式:
--     数据库不存在:解开顶部 CREATE DATABASE / USE
--     数据库已存在:仅执行下方 CREATE TABLE 部分(全部 IF NOT EXISTS)
-- =====================================================================

-- CREATE DATABASE IF NOT EXISTS prismatica DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci;
-- USE prismatica;

-- ---------------------------------------------------------------------
-- 1. user_accounts — 用户账户主表
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_accounts (
    user_id        CHAR(36) PRIMARY KEY,
    display_name   VARCHAR(64) NOT NULL,
    tier           VARCHAR(16) NOT NULL DEFAULT 'beta',
    status         VARCHAR(16) NOT NULL DEFAULT 'active',  -- active / suspended / expired
    activated_at   DATETIME(3) NOT NULL,
    expire_at      DATETIME(3) NULL,
    created_at     DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
    updated_at     DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    KEY idx_user_accounts_status (status),
    KEY idx_user_accounts_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户账户';

-- ---------------------------------------------------------------------
-- 2. user_devices — 用户设备(多设备登录)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_devices (
    device_id      CHAR(36) PRIMARY KEY,
    user_id        CHAR(36) NOT NULL,
    device_name    VARCHAR(128) NOT NULL DEFAULT '',
    platform       VARCHAR(32) NOT NULL DEFAULT '',
    first_seen_at  DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
    last_seen_at   DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    CONSTRAINT fk_user_devices_user FOREIGN KEY (user_id) REFERENCES user_accounts(user_id),
    KEY idx_user_devices_user (user_id, last_seen_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户设备';

-- ---------------------------------------------------------------------
-- 3. user_balances — 用户余额(1:1 行锁粒度)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_balances (
    user_id         CHAR(36) PRIMARY KEY,
    balance         BIGINT NOT NULL DEFAULT 0,
    frozen_balance  BIGINT NOT NULL DEFAULT 0,
    total_spent     BIGINT NOT NULL DEFAULT 0,
    total_recharged BIGINT NOT NULL DEFAULT 0,
    version         INT NOT NULL DEFAULT 0,  -- 乐观锁
    updated_at      DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    CONSTRAINT fk_user_balances_user FOREIGN KEY (user_id) REFERENCES user_accounts(user_id),
    CONSTRAINT chk_user_balances_balance CHECK (balance >= 0),
    CONSTRAINT chk_user_balances_frozen CHECK (frozen_balance >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户余额';

-- ---------------------------------------------------------------------
-- 4. license_codes — 凭证签发持久化(2026-08-06 新增)
--    issued 立即入库;消费时由事务写 consumed_at / consumed_by_user_id。
--    明文 code 仅在签发响应里一次性返回,表内只存 sha256 hash。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS license_codes (
    code_hash           CHAR(64) PRIMARY KEY,
    code_kind           VARCHAR(16) NOT NULL,  -- invite / trial / recharge
    status              VARCHAR(16) NOT NULL DEFAULT 'active',  -- active / consumed / revoked / expired
    -- invite/trial 字段
    granted_balance     INT NULL,
    granted_days        INT NULL,
    tier                VARCHAR(16) NULL,
    -- recharge 字段
    amount              INT NULL,
    -- 元数据
    issued_by           VARCHAR(64) NOT NULL DEFAULT '',
    issued_at           DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
    expire_at           DATETIME(3) NULL,
    consumed_at         DATETIME(3) NULL,
    consumed_by_user_id CHAR(36) NULL,
    consumed_ip         VARCHAR(64) NULL,
    -- 仅在 issue 时一次性返回的明文 code(后续不可再查;废弃/补发请重新 issue)
    raw_code_signature  VARCHAR(512) NULL,
    CONSTRAINT chk_license_codes_kind CHECK (code_kind IN ('invite','trial','recharge')),
    CONSTRAINT chk_license_codes_status CHECK (status IN ('active','consumed','revoked','expired')),
    CONSTRAINT chk_license_codes_amount CHECK (amount IS NULL OR amount > 0),
    KEY idx_license_codes_kind_status (code_kind, status),
    KEY idx_license_codes_issued (issued_at),
    KEY idx_license_codes_consumed (consumed_by_user_id, consumed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='凭证签发持久化';

-- ---------------------------------------------------------------------
-- 5. bills — 账单流水
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
    status               VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending / settled / refunded
    task_id              CHAR(36) NOT NULL DEFAULT '',
    description          VARCHAR(256) NOT NULL DEFAULT '',
    idempotency_key      CHAR(36) NULL,
    created_at           DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
    settled_at           DATETIME(3) NULL,
    CONSTRAINT fk_bills_user FOREIGN KEY (user_id) REFERENCES user_accounts(user_id),
    UNIQUE KEY uk_bills_idem (idempotency_key),
    KEY idx_bills_user_status (user_id, status, created_at),
    KEY idx_bills_status_created (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='账单流水';

-- ---------------------------------------------------------------------
-- 6. recharge_records — 充值/赠送记录
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
    expire_at      DATETIME(3) NULL,
    created_at     DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
    CONSTRAINT fk_recharge_records_user FOREIGN KEY (user_id) REFERENCES user_accounts(user_id),
    CONSTRAINT chk_recharge_amount CHECK (amount > 0),
    KEY idx_recharge_records_user (user_id, created_at),
    KEY idx_recharge_records_source (source, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='充值/赠送流水';

-- ---------------------------------------------------------------------
-- 7. refresh_tokens — Refresh Token(允许主动 revoke)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_id     CHAR(36) PRIMARY KEY,
    user_id      CHAR(36) NOT NULL,
    device_id    CHAR(36) NOT NULL,
    expires_at   DATETIME(3) NOT NULL,
    revoked_at   DATETIME(3) NULL,
    created_at   DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
    CONSTRAINT fk_refresh_user FOREIGN KEY (user_id) REFERENCES user_accounts(user_id),
    KEY idx_refresh_user (user_id, expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Refresh Token';

-- ---------------------------------------------------------------------
-- 8. audit_logs — 审计日志(所有 admin 行为 + 关键业务行为)
--    actor:管理员 username;system 表示系统自动行为
--    action:命名空间式 action,例:admin.login_success / admin.grant_balance
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_logs (
    audit_id     BIGINT AUTO_INCREMENT PRIMARY KEY,
    actor        VARCHAR(64) NOT NULL,
    action       VARCHAR(64) NOT NULL,
    target_user  CHAR(36) NULL,
    details      JSON NULL,
    ip           VARCHAR(64) NULL,
    created_at   DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
    KEY idx_audit_actor_time (actor, created_at),
    KEY idx_audit_action_time (action, created_at),
    KEY idx_audit_target_time (target_user, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='审计日志';

-- ---------------------------------------------------------------------
-- 9. admin_users — 管理后台账号(浏览器 cookie + X-Admin-Token 双轨)
--    role:admin / super_admin(本期统一 admin,留扩展位)
--    status:active / locked / disabled
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin_users (
    user_id          CHAR(36) PRIMARY KEY,
    username         VARCHAR(64) NOT NULL,
    password_hash    VARCHAR(255) NOT NULL,
    role             VARCHAR(32) NOT NULL DEFAULT 'admin',
    status           VARCHAR(16) NOT NULL DEFAULT 'active',
    last_login_at    DATETIME(3) NULL,
    failed_attempts  INT NOT NULL DEFAULT 0,
    created_at       DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
    updated_at       DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    UNIQUE KEY uk_admin_users_username (username),
    KEY idx_admin_users_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='管理后台账号';