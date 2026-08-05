# PRD v2 — Prismatica 云端后端（用户登录 + 积分结算）

> 用途：**给另一个负责后端的 AI** 提供完整 PRD，让其实现 FastAPI + MySQL 后端；
> 同时给**前端 AI**（即本仓库当前会话）提供对接契约。
>
> 现状：所有账户/账单/凭证在本地 SQLite。本期把"用户登录"和"积分结算"两条核心业务迁移到云端。
> 范围：**用户登录 + 积分结算**；不包括 HSK 下载/语料分析等业务功能。
>
> 部署：GitHub Actions CI/CD + Fly.io / AWS 运行 FastAPI，MySQL 8 持久化。
> 认证：保留现有 INV/TRY/RCH 三种凭证码（HMAC-SHA256），云端验签后下发 JWT；前端后续请求带 JWT。

---

## 0. TL;DR

**核心策略**：把现有本地 `account_db` / `auth_service` 中"用户身份"+"积分账户"+"账单流水"三条主线抽到云端。前端契约（`AuthService`、`BillingService`、`signalBus` 信号、UI 弹窗文案）**完全不变**；仅替换内部实现为 HTTP 调用。

**两个 AI 必须对齐的事**：
1. **API 路径与 JSON Schema** —— 本 PRD §5 是合同
2. **错误码与文案** —— §6
3. **JWT 字段** —— §7
4. **幂等 / 重放保护** —— §8
5. **本地缓存与离线降级** —— §9

---

## 1. 背景

### 1.1 现状

```
┌──────────────────────────────────────────────────────┐
│ Frontend (PySide6 + qfluentwidgetspro)                │
│                                                      │
│  LoginDialog ─► AuthService ─► license.enc (本地)    │
│  RechargeDialog ─► AuthService ─► signed_code 验证    │
│  @charged 装饰器 ─► BillingService ─► account_db      │
│                (本地 SQLite datas/account.db)        │
└──────────────────────────────────────────────────────┘
```

- 凭证验证：`HMAC-SHA256`，密钥 `LICENSE_SECRET`（环境变量）
- 三种码：邀请码 `INV-XXXX-XXXX-XXXX-XXXX` / 体验码 `TRY-…` / 充值码 `RCH-…`
- 账户余额/账单/充值流水：本地 SQLite，单机单账户

### 1.2 痛点

| 痛点 | 现状 | 影响 |
|------|------|------|
| 换设备余额丢失 | 余额仅在本机 SQLite | 用户换电脑要重新充值 |
| 运营签发码不可追踪 | `recharge_codes` 表仅本地去重 | 不知道哪些码被消费了 |
| 多人/多设备账号 | "一机一账户"硬编码 | 内测期就要淘汰 |
| 余额可被本地修改 | 直接编辑 SQLite 即可 | 运营审计缺失 |
| 退款/调整无审计 | 流水在本机 | 无法做风控 |

### 1.3 目标

- **用户身份**：云端账户（每用户 UUID，可多设备登录）
- **积分结算**：云端 ledger，所有流水 server-authoritative
- **凭证签发**：保留本地 `tools/ops.py` 生成三类码；码验证移到云端（HMAC 密钥由后端持有）
- **过渡期**：内测期保留本地 SQLite 作为**只读缓存**（离线时仍可查看）；写操作强制走云端
- **部署**：MySQL 8 + FastAPI，GitHub Actions CI 跑测试，main 分支 push 触发 Fly.io / AWS 部署

---

## 2. 范围与非范围

### 2.1 本期 In-Scope

| 模块 | 说明 |
|------|------|
| 云端账户（user / device / session） | 多设备登录、JWT 鉴权 |
| 三种码（INV/TRY/RCH）的云端验证 | HMAC 密钥在后端,前端不持有 |
| 充值（recharge）| RCH 码 → 用户余额 +1,server-authoritative |
| 邀请激活 / 体验激活 | 创建 user account + 赠送余额 |
| 扣费（estimate/preauth/settle/refund）| 与前端 `BillingService` 完全对齐 |
| 账单查询 | 用户流水拉取（本地 SQLite 角色降级为缓存）|
| 运营端 sign-in (admin role) | 简单 `X-Admin-Token` 头校验,无 admin UI |
| OpenAPI 3.1 schema | `/openapi.json` 自动生成,供前端类型生成 |
| Prometheus `/metrics` | 监控 QPS / 错误率 |

### 2.2 Out-of-Scope（明确不做）

| 不做 | 原因 |
|------|------|
| 项目/语料/任务同步 | 业务功能,二期再做 |
| 退款/调整 admin UI | 仅提供 SQL/CLI,前端 admin UI 二期 |
| 实时计价的可视化监控 | 二期 |
| 多区域容灾 | 单区域单实例足够内测期 |

---

## 3. 用户旅程（迁移后）

### 3.1 首次激活

```
1. 用户启动软件 → SplashWindow → AuthGate(若本地无 license.enc)
2. LoginDialog → 用户输入 INV 码 → 点激活
3. 前端把 INV 码 POST /v1/auth/redeem
4. 云端:
   a. 用 LICENSE_SECRET 验签 HMAC
   b. 检查码是否被消费 → 是 → 409 ALREADY_USED
   c. 创建 user_accounts 行（userId = UUID）
   d. 创建/累计 user_balances（按 grantedBalance 入账）
   e. 记录 activation_grant 流水
   f. 标记 INV 码已用
5. 返回 {accessToken: JWT, refreshToken, user: {...}}
6. 前端存 JWT 到 config/license.enc（AES-GCM 加密,沿用）
7. signalBus.activationStatusChanged.emit(True)
8. 主窗口继续
```

### 3.2 充值

```
1. RechargeDialog → 用户输入 RCH 码 → 点确认
2. 前端 POST /v1/billing/recharge {code, deviceId}
   Header: Authorization: Bearer <JWT>
3. 云端:
   a. 校验 JWT → 取 userId
   b. 验签 HMAC
   c. 检查码全局幂等表 → 已用 → 409
   d. user_balances.balance += amount (MySQL 行锁)
   e. 写 recharge_records 流水
   f. 返回 {success, balanceAfter}
4. 前端刷新本地 BalanceCard
```

### 3.3 扣费（与本地 @charged 装饰器对齐）

```
1. 前端调用 BillingService.estimate(actionType, resourceUsed)
   → 云端 POST /v1/billing/estimate
   → 返回 CostPreview（与现有 Pydantic 模型完全一致）
2. 前端调用 BillingService.preauth(...) → POST /v1/billing/preauth
   → 返回 {billId, estimatedCost, balanceAfter}
3. 业务执行（本地跑 freq_analyze / corpus_import ...）
4. 前端调用 BillingService.settle(billId, realCost) → POST /v1/billing/settle
   → 云端结算（realCost 退/补差额）,返回 {realCost, balanceAfter}
5. 失败时 BillingService.refund(billId) → POST /v1/billing/refund
   → 云端撤销预占
```

### 3.4 跨设备

```
- 设备 A 激活 → JWT-A
- 设备 B 启动 → AuthGate → LoginDialog → 用户再次输入相同 INV 码
  → 云端检查:同一 userId? 是 → 返回 JWT-B(userId 不变)
  → 设备 B 也激活同一账户
- 多设备并发扣费:云端行锁 + 余额 CHECK >= 0 防超扣
```

---

## 4. 系统架构

### 4.1 部署拓扑

```
                    ┌──────────────────────────────┐
                    │   GitHub Repository           │
                    │   (FastAPI + MySQL schema)    │
                    └──────────────┬───────────────┘
                                   │ push main
                                   ▼
                    ┌──────────────────────────────┐
                    │   GitHub Actions             │
                    │   - pytest                   │
                    │   - ruff                     │
                    │   - docker build → registry  │
                    │   - deploy to Fly.io         │
                    └──────────────┬───────────────┘
                                   ▼
              ┌────────────────────────────────────────┐
              │   Fly.io (or AWS ECS)                  │
              │   ┌────────────────────────────────┐   │
              │   │  FastAPI + uvicorn (1~2 实例)  │   │
              │   │  - /v1/auth/*                   │   │
              │   │  - /v1/billing/*                │   │
              │   │  - /healthz                     │   │
              │   │  - /metrics                     │   │
              │   │  - /openapi.json                │   │
              │   └─────────────┬──────────────────┘   │
              └────────────────┼──────────────────────┘
                               │ TLS
                               ▼
              ┌────────────────────────────────────────┐
              │   MySQL 8 (RDS / Fly Postgres → MySQL)│
              │   - user_accounts                      │
              │   - user_devices                       │
              │   - user_balances                      │
              │   - recharge_codes (issued/used)       │
              │   - bills                              │
              │   - recharge_records                   │
              │   - license_codes_seen (幂等)          │
              │   - audit_logs                         │
              └────────────────────────────────────────┘
```

### 4.2 前端 ↔ 后端契约层

```
Frontend                                         Backend
───────────                                       ────────
PySide6 App   ──HTTP/JSON (HTTPS)──►   FastAPI (FastAPI app)
                  Bearer JWT                       + SQLAlchemy + asyncmy
                  X-Request-Id                     + pydantic v2
                  X-Device-Id
```

### 4.3 项目结构（后端仓库 `prismatica-backend/`）

```
prismatica-backend/
├── pyproject.toml
├── README.md
├── alembic.ini
├── alembic/
│   ├── env.py
│   └── versions/
│       └── 001_init.py
├── app/
│   ├── main.py                  # FastAPI app + middleware
│   ├── config.py                # pydantic-settings
│   ├── deps.py                  # Depends(...) helpers (auth, db, admin)
│   ├── db.py                    # async SQLAlchemy session
│   ├── security/
│   │   ├── jwt.py               # encode/decode JWT (HS256)
│   │   ├── hmac.py              # HMAC-SHA256 验签(复用 signed_code 思路)
│   │   └── password.py          # (预留二期,本期不做密码登录)
│   ├── models/
│   │   ├── user_account.py
│   │   ├── user_device.py
│   │   ├── user_balance.py
│   │   ├── recharge_code.py
│   │   ├── bill.py
│   │   ├── recharge_record.py
│   │   ├── license_code_seen.py
│   │   └── audit_log.py
│   ├── schemas/
│   │   ├── auth.py              # RedeemRequest/Response, RefreshRequest
│   │   ├── billing.py           # CostPreview, PreauthReq, SettleReq, RefundReq
│   │   ├── user.py              # UserOut
│   │   └── errors.py            # ApiError envelope
│   ├── routers/
│   │   ├── auth.py              # /v1/auth/redeem, /v1/auth/refresh
│   │   ├── billing.py           # /v1/billing/{estimate,preauth,settle,refund}
│   │   ├── account.py           # /v1/account/me, /v1/account/bills
│   │   └── admin.py             # /v1/admin/* (X-Admin-Token)
│   ├── services/
│   │   ├── auth_service.py
│   │   ├── billing_service.py
│   │   └── pricing.py           # 复用前端 DEFAULT_RULES
│   └── middleware/
│       ├── request_id.py
│       ├── rate_limit.py        # slowapi (60 req/min per device)
│       └── access_log.py
└── tests/
    ├── conftest.py
    ├── test_auth.py
    ├── test_billing.py
    └── test_admin.py
```

---

## 5. API 契约（**前后端对接合同**）

### 5.1 通用约定

- **Base URL**：`https://api.prismatica.app/v1`（占位,部署后替换）
- **鉴权头**：
  ```
  Authorization: Bearer <jwt>
  X-Device-Id: <uuid>           # 用于多设备追踪与限速
  X-Request-Id: <uuid>          # 用于日志追踪,前端不强制生成
  X-Client-Version: 2.0.0       # 前端版本号
  ```
- **响应封装**：
  - 成功：`200/201` 直接返回资源 JSON
  - 失败：`4xx/5xx` 返回 `ApiError` envelope：
    ```json
    {
      "error": {
        "code": "ALREADY_USED",
        "message": "该充值码已被使用",
        "requestId": "uuid",
        "details": {}            // 可选,字段级错误
      }
    }
    ```
- **时间格式**：ISO 8601 字符串（`2026-08-05T12:34:56Z`）
- **金额**：整数（最小单位 = 1 币）
- **金额操作**：MySQL 行锁 + 触发器 `CHECK (balance >= 0)` 防超扣

### 5.2 认证（Auth）

#### `POST /v1/auth/redeem`

请求：
```json
{
  "code": "eyJ...",                 // base64 编码的 InviteCode/TrialCode/RechargeCode
  "deviceId": "uuid",
  "deviceName": "Windows-Desktop-XYZ",
  "displayName": "内测用户"
}
```

响应 200（激活成功 / 充值成功）：
```json
{
  "mode": "invite",                 // invite / trial / recharge
  "user": {
    "userId": "uuid",
    "displayName": "内测用户",
    "tier": "beta",
    "createdAt": "2026-08-05T12:34:56Z"
  },
  "balance": {
    "balance": 100,
    "frozenBalance": 0,
    "totalSpent": 0,
    "totalRecharged": 100
  },
  "tokens": {
    "accessToken": "eyJ...",         // JWT, 有效期 1h
    "refreshToken": "uuid",          // opaque, 有效期 30d
    "expiresIn": 3600
  }
}
```

错误码：

| HTTP | error.code | 触发条件 | 前端处理 |
|------|-----------|---------|---------|
| 400 | `INVALID_CODE` | 码格式/签名错误 | InfoBar.error |
| 401 | `EXPIRED` | 码已过期 | InfoBar.error |
| 409 | `ALREADY_USED` | 充值码已用过 | InfoBar.error + 不显示 reactivateRow |
| 409 | `ALREADY_AUTHENTICATED` | 邀请/体验激活时 user 已有未过期凭证 | 弹「立即注销并重试」按钮 |
| 429 | `RATE_LIMITED` | 同 deviceId 1 分钟内 > 10 次 | 退避 + 提示 |

#### `POST /v1/auth/refresh`

请求：`{"refreshToken": "uuid"}`
响应：与 redeem 同结构（tokens 块）
错误：`401 REFRESH_INVALID` / `401 REFRESH_EXPIRED`

#### `POST /v1/auth/logout`（可选,本期可不做）

请求：`{}` + JWT
响应：`204 No Content`

### 5.3 账户（Account）

#### `GET /v1/account/me`

响应 200：
```json
{
  "userId": "uuid",
  "displayName": "内测用户",
  "tier": "beta",
  "balance": 100,
  "frozenBalance": 0,
  "totalSpent": 0,
  "totalRecharged": 100,
  "activatedAt": "2026-08-05T12:34:56Z",
  "expireAt": "2026-09-05T12:34:56Z"
}
```

#### `GET /v1/account/bills?cursor=&limit=50`

响应 200：
```json
{
  "items": [
    {
      "billId": "uuid",
      "actionType": "freq_analyze",
      "actionDisplayName": "词频分析",
      "estimatedCost": 5,
      "realCost": 4,
      "resourceUsed": 3200,
      "balanceBefore": 100,
      "balanceAfter": 96,
      "status": "settled",
      "taskId": "",
      "description": "",
      "createdAt": "2026-08-05T13:00:00Z",
      "settledAt": "2026-08-05T13:00:05Z"
    }
  ],
  "nextCursor": null
}
```

### 5.4 计费（Billing）

所有接口均需 `Authorization: Bearer <jwt>`。

#### `POST /v1/billing/estimate`

请求：
```json
{
  "actionType": "freq_analyze",
  "resourceUsed": 3200
}
```

响应 200：`CostPreview`（与前端 `app/core/models/billing_models.py` 完全对齐）

#### `POST /v1/billing/preauth`

请求：
```json
{
  "actionType": "freq_analyze",
  "resourceUsed": 3200,
  "taskId": "uuid",
  "description": "用户自定义描述"
}
```

响应 200：
```json
{
  "billId": "uuid",
  "estimatedCost": 5,
  "balanceAfter": 95
}
```

错误：`402 INSUFFICIENT_BALANCE`（带 `currentBalance`, `required` 字段）

#### `POST /v1/billing/settle`

请求：
```json
{
  "billId": "uuid",
  "realCost": 4,
  "resourceUsed": 3100
}
```

响应 200：
```json
{
  "billId": "uuid",
  "realCost": 4,
  "balanceAfter": 96,
  "refunded": 1
}
```

错误：`409 BILL_ALREADY_SETTLED` / `409 BILL_NOT_FOUND`

#### `POST /v1/billing/refund`

请求：`{"billId": "uuid"}`
响应 200：`{"billId": "uuid", "refundedAmount": 5, "balanceAfter": 100}`
错误：`409 BILL_ALREADY_SETTLED` / `409 BILL_NOT_PENDING`

### 5.5 Admin（运营 CLI 调）

需要 `X-Admin-Token: $ADMIN_TOKEN` 头,IP 白名单（可选）。

#### `POST /v1/admin/grant`

请求：
```json
{
  "userId": "uuid",
  "amount": 100,
  "note": "客服补偿"
}
```

响应 200：`{"userId": "uuid", "newBalance": 200}`

#### `POST /v1/admin/issue-codes`

请求：
```json
{
  "kind": "invite",
  "count": 10,
  "grantedBalance": 100,
  "grantedDays": 30,
  "tier": "beta",
  "expireDays": 14
}
```

响应 200：
```json
{
  "codes": ["eyJ...", "eyJ...", ...]
}
```

注：admin 端也用相同的 `makeInviteCode` 等函数,**HMAC 密钥必须在后端**,通过 LICENSE_SECRET 环境变量注入。

---

## 6. 错误码与文案（**前端 InfoBar 文案必须对齐**）

| error.code | 中文文案（前端直接显示）| 英文文案 |
|-----------|---------------------|---------|
| `INVALID_CODE` | 码无效或已损坏 | Code invalid or corrupted |
| `EXPIRED` | 该凭证已过期 | Code expired |
| `ALREADY_USED` | 该充值码已被使用 | Code already used |
| `ALREADY_AUTHENTICATED` | 已存在激活凭证,请先注销后再兑换 | Already authenticated |
| `NEED_ACTIVATION` | 请先激活后再使用充值码 | Activation required |
| `INSUFFICIENT_BALANCE` | 余额不足 | Insufficient balance |
| `BILL_NOT_FOUND` | 账单不存在 | Bill not found |
| `BILL_ALREADY_SETTLED` | 账单已结算 | Bill already settled |
| `BILL_NOT_PENDING` | 账单不在待结算状态 | Bill not pending |
| `RATE_LIMITED` | 请求过于频繁,请稍后再试 | Rate limited |
| `INTERNAL_ERROR` | 服务暂时不可用,请稍后再试 | Internal server error |

---

## 7. JWT 字段定义

```json
{
  "sub": "uuid",                    // userId
  "did": "uuid",                    // deviceId
  "tier": "beta",                   // 用户档位
  "iat": 1700000000,                // issued at
  "exp": 1700003600,                // 1 小时
  "iss": "prismatica-api",
  "aud": "prismatica-client",
  "ver": "v2"
}
```

签名算法：**HS256**,密钥为 `JWT_SECRET`（与 `LICENSE_SECRET` 不同的环境变量）。

Refresh Token：**opaque UUID**,存储在 `refresh_tokens` 表（userId / deviceId / expiresAt / revokedAt）。

---

## 8. 幂等与重放保护

| 端点 | 幂等机制 |
|------|---------|
| `POST /v1/auth/redeem` | `Idempotency-Key` 头（UUID,前端用 `deviceId + code` 哈希生成） + `license_codes_seen(code, hash_idem)` 唯一约束 |
| `POST /v1/billing/preauth` | `Idempotency-Key` 头 + `bills.idempotency_key` 唯一约束 |
| `POST /v1/billing/settle` | 同一 `billId` 重复结算 → 409 |
| `POST /v1/billing/refund` | 同一 `billId` 重复退款 → 409 |

`license_codes_seen` 表：
```sql
CREATE TABLE license_codes_seen (
  code_hash CHAR(64) PRIMARY KEY,   -- sha256(code), 索引
  code_kind ENUM('invite','trial','recharge'),
  consumed_by_user_id CHAR(36),
  consumed_at TIMESTAMP,
  -- 充值码额外:
  recharge_user_id CHAR(36),
  recharge_amount INT
);
```

---

## 9. 前端缓存与离线降级（**前端必读**）

| 场景 | 前端行为 |
|------|---------|
| 启动时无网络 | 仍可启动,使用本地 `license.enc` 中的 JWT;但 `GET /v1/account/me` 失败 → 弹"云端不可达"提示,业务功能降级 |
| 业务执行中网络断开 | 估算/预占可走本地缓存（`pricing_service` 已实现）;结算必须等网络恢复 |
| 余额查询失败 | 显示"---" + 「重试」按钮 |
| 后端 5xx | 退避 3 次后提示用户,不让前端崩溃 |

**前端必须新增的本地缓存**（**这是前端的实施任务**）：
- `cache/user.json` —— 上次成功的 `GET /v1/account/me` 结果
- `cache/bills.json` —— 上次拉取的最近 50 条账单
- 启动时优先读 cache,后台异步刷云端

---

## 10. 数据库 Schema（**后端必建表**）

> 数据库：MySQL 8.0，字符集 utf8mb4，排序规则 utf8mb4_unicode_ci。

```sql
-- 用户账户
CREATE TABLE user_accounts (
  user_id        CHAR(36) PRIMARY KEY,
  display_name   VARCHAR(64) NOT NULL,
  tier           VARCHAR(16) NOT NULL DEFAULT 'beta',
  status         VARCHAR(16) NOT NULL DEFAULT 'active',  -- active/suspended/expired
  activated_at   TIMESTAMP NOT NULL,
  expire_at      TIMESTAMP NULL,
  created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_user_accounts_status (status)
);

-- 设备（多设备登录）
CREATE TABLE user_devices (
  device_id      CHAR(36) PRIMARY KEY,
  user_id        CHAR(36) NOT NULL,
  device_name    VARCHAR(128) NOT NULL DEFAULT '',
  platform       VARCHAR(32) NOT NULL DEFAULT '',
  first_seen_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_seen_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_user_devices_user FOREIGN KEY (user_id) REFERENCES user_accounts(user_id),
  KEY idx_user_devices_user (user_id, last_seen_at)
);

-- 用户余额(1:1 with user_accounts,行锁粒度)
CREATE TABLE user_balances (
  user_id         CHAR(36) PRIMARY KEY,
  balance         BIGINT NOT NULL DEFAULT 0 CHECK (balance >= 0),
  frozen_balance  BIGINT NOT NULL DEFAULT 0 CHECK (frozen_balance >= 0),
  total_spent     BIGINT NOT NULL DEFAULT 0,
  total_recharged BIGINT NOT NULL DEFAULT 0,
  version         INT NOT NULL DEFAULT 0,  -- 乐观锁
  updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_user_balances_user FOREIGN KEY (user_id) REFERENCES user_accounts(user_id)
);

-- 充值码去重（全局幂等）
CREATE TABLE license_codes_seen (
  code_hash             CHAR(64) PRIMARY KEY,
  code_kind             ENUM('invite','trial','recharge') NOT NULL,
  issued_at             TIMESTAMP NULL,
  consumed_at           TIMESTAMP NULL,
  consumed_by_user_id   CHAR(36) NULL,
  consume_ip            VARCHAR(64) NULL,
  -- 充值码额外:
  recharge_user_id      CHAR(36) NULL,
  recharge_amount       INT NULL,
  expire_at             TIMESTAMP NULL,
  KEY idx_codes_seen_user (consumed_by_user_id, consumed_at),
  KEY idx_codes_seen_kind (code_kind, expire_at)
);

-- 账单流水
CREATE TABLE bills (
  bill_id              CHAR(36) PRIMARY KEY,
  user_id              CHAR(36) NOT NULL,
  action_type          VARCHAR(32) NOT NULL,
  action_display_name  VARCHAR(64) NOT NULL DEFAULT '',
  estimated_cost       INT NOT NULL DEFAULT 0,
  real_cost            INT NOT NULL DEFAULT 0,
  resource_used        BIGINT NOT NULL DEFAULT 0,
  balance_before       BIGINT NOT NULL DEFAULT 0,
  balance_after        BIGINT NOT NULL DEFAULT 0,
  status               VARCHAR(16) NOT NULL DEFAULT 'pending',
  task_id              CHAR(36) NOT NULL DEFAULT '',
  description          VARCHAR(256) NOT NULL DEFAULT '',
  idempotency_key      CHAR(36) NULL,
  created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  settled_at           TIMESTAMP NULL,
  CONSTRAINT fk_bills_user FOREIGN KEY (user_id) REFERENCES user_accounts(user_id),
  UNIQUE KEY uk_bills_idem (idempotency_key),
  KEY idx_bills_user_status (user_id, status, created_at)
);

-- 充值/赠送记录
CREATE TABLE recharge_records (
  record_id      CHAR(36) PRIMARY KEY,
  user_id        CHAR(36) NOT NULL,
  amount         INT NOT NULL CHECK (amount > 0),
  source         VARCHAR(32) NOT NULL,  -- activation_grant / recharge_code / manual_gift / admin_grant
  code_hash      CHAR(64) NULL,         -- 充值码相关
  operator_note  VARCHAR(256) NOT NULL DEFAULT '',
  balance_before BIGINT NOT NULL DEFAULT 0,
  balance_after  BIGINT NOT NULL DEFAULT 0,
  expire_at      TIMESTAMP NULL,
  created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_recharge_records_user FOREIGN KEY (user_id) REFERENCES user_accounts(user_id),
  KEY idx_recharge_records_user (user_id, created_at)
);

-- Refresh Token 表
CREATE TABLE refresh_tokens (
  token_id     CHAR(36) PRIMARY KEY,
  user_id      CHAR(36) NOT NULL,
  device_id    CHAR(36) NOT NULL,
  expires_at   TIMESTAMP NOT NULL,
  revoked_at   TIMESTAMP NULL,
  created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_refresh_user FOREIGN KEY (user_id) REFERENCES user_accounts(user_id),
  KEY idx_refresh_user (user_id, expires_at)
);

-- 审计日志（所有 admin 行为）
CREATE TABLE audit_logs (
  audit_id     BIGINT AUTO_INCREMENT PRIMARY KEY,
  actor        VARCHAR(64) NOT NULL,   -- admin token id or system
  action       VARCHAR(64) NOT NULL,
  target_user  CHAR(36) NULL,
  details      JSON NULL,
  ip           VARCHAR(64) NULL,
  created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  KEY idx_audit_actor_time (actor, created_at)
);
```

---

## 11. 环境变量（**后端必读**）

```env
# MySQL
DB_HOST=...
DB_PORT=3306
DB_NAME=prismatica
DB_USER=...
DB_PASSWORD=...

# 凭证签名(与前端 signed_code.py 共用)
LICENSE_SECRET=<64hex>           # HMAC-SHA256 共享密钥

# JWT
JWT_SECRET=<64hex>               # HS256 签名密钥(与 LICENSE_SECRET 不同)
JWT_ALG=HS256
JWT_ACCESS_TTL=3600              # 1 小时
JWT_REFRESH_TTL=2592000          # 30 天

# Admin
ADMIN_TOKEN=<random>

# 限速
RATE_LIMIT_PER_MIN=60
```

---

## 12. CI/CD 流水线（**后端必读**）

`.github/workflows/ci.yml`：

```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    services:
      mysql:
        image: mysql:8.0
        env:
          MYSQL_ROOT_PASSWORD: test
          MYSQL_DATABASE: prismatica_test
        ports: ['3306:3306']
        options: --health-cmd="mysqladmin ping" --health-interval=5s
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -e .[dev]
      - run: pytest --cov=app --cov-fail-under=80
      - run: ruff check app/

  deploy:
    if: github.ref == 'refs/heads/main'
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: |
          curl -L https://fly.io/install.sh | sh
          flyctl deploy --remote-only
    env:
      FLY_API_TOKEN: ${{ secrets.FLY_API_TOKEN }}
```

---

## 13. 里程碑（**协调两个 AI 的任务边界**）

### 13.1 后端 AI 任务（**M1-M4**）

| ID | 任务 | 验收 |
|----|------|------|
| M1 | 仓库骨架 + FastAPI hello world + MySQL 连接 + Alembic init + 7 张表迁移 | `GET /healthz` 200,`alembic upgrade head` 成功 |
| M2 | `/v1/auth/redeem` + `/v1/auth/refresh` + LICENSE_SECRET HMAC 验签 | pytest 通过（覆盖 INV/TRY/RCH/ALREADY_USED/EXPIRED）|
| M3 | `/v1/billing/{estimate,preauth,settle,refund}` + 行锁 + 幂等 | pytest 通过（覆盖 INSUFFICIENT/双重结算） |
| M4 | `/v1/account/me` + `/v1/account/bills` + `/v1/admin/*` + rate limit + OpenAPI + Dockerfile + GitHub Actions CI | 部署到 Fly.io,前端能 ping 通 |

### 13.2 前端 AI 任务（**本仓库会话**）

| ID | 任务 | 验收 |
|----|------|------|
| F1 | 新增 `app/core/services/cloud_api.py`：HTTP 客户端（httpx）+ JWT 管理 + 错误码映射 | 单元测试覆盖 6 个端点 mock |
| F2 | 改造 `AuthService.redeemCode` → 调 `/v1/auth/redeem`（本地 SQLite 降级为只读缓存）| 不影响 `isAuthenticated()` / `signalBus` 契约 |
| F3 | 改造 `BillingService.{estimate,preauth,settle,refund}` → 调云端（保留本地缓存）| 不影响 `signalBus.balanceChanged` / `InsufficientBalanceError` 契约 |
| F4 | `account_interface.py` 余额/账单展示走 `GET /v1/account/{me,bills}`,本地 SQLite 仅作离线 cache | UI 不变 |
| F5 | `AuthGate` 启动门 + 凭证损坏横幅 → 调云端,网络断开时回退到本地 license.enc | 离线/在线两种场景均测试 |

**F1-F5 完成后,前后端 e2e 联调**:前端拿一个 RCH 码 → 后端入库 → 前端刷新余额 → 一致。**

---

## 14. 风险与缓解

| 风险 | 缓解 |
|------|------|
| LICENSE_SECRET 泄露 → 任意签发码 | 与 JWT_SECRET 分离,后端独立管理,rotate 流程文档化 |
| 余额并发超扣 | MySQL 行锁 + `CHECK (balance >= 0)` + 触发器 |
| refresh token 泄露 | 30d TTL + 单设备可主动 revoke |
| 网络抖动导致重复扣费 | 幂等键 + bills.status 状态机 |
| 云端不可用 | 前端本地 SQLite 缓存只读,业务功能降级（不可扣费） |
| HMAC 密钥同步 | 前后端共用 LICENSE_SECRET,**通过环境变量在两端都注入**;不可写死在源码 |

---

## 15. 文档同步

- 本 PRD 一旦变更,必须同步更新：
  - `prismatica/PRD_v2_云端化.md`（本仓库新增的同级文件）
  - 前后端 README 中的对接章节
- 接口变更通过 OpenAPI 自动生成,前端用 `openapi-python-client` 生成 SDK