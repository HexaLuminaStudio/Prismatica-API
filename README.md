# Prismatica Backend

Prismatica 桌面客户端的云端后端(对齐 `docs/backend-prd.md` PRD v2)。

- **范围**:用户登录 + 积分结算(redeem / refresh / estimate / preauth / settle / refund / account / admin)
- **技术栈**:Flask 3 + SQLAlchemy 2.x(同步)+ PyMySQL + PyJWT + bcrypt + pydantic v2 + loguru + Flask-Limiter + prometheus-client
- **持久化**:MySQL 8.0（版本化迁移；P0-A 为 14 张业务表 + `schema_migrations`）
- **部署**:Docker + docker-compose(Flask + MySQL + Redis)

## 快速启动

### 方式一:本地直接跑

```bash
# 1. 装依赖(uv 推荐)
uv sync
# 或 pip install -e .[dev]

# 2. 复制并修改 .env
cp .env.example .env
# 填入 LICENSE_SECRET / JWT_SECRET / ADMIN_TOKEN / DB_PASSWORD

# 3. 准备空 MySQL 数据库并执行版本化迁移（可安全重复运行）
python -m scripts.migrate_account_billing
python -m scripts.db_preflight

# 4. 启动（P0-A M2-M9 完成前，旧 ORM 尚未适配新 schema）
python -m app.main
# 或生产模式
gunicorn -w 4 -b 0.0.0.0:8000 'app.main:app'
```

### 方式二:Docker Compose

```bash
cd deploy
docker compose up -d --build
# 等待 mysql 健康检查通过后,API 自动连上
curl http://localhost:8000/healthz
```

## 目录结构

```
app/
├── config.py        12-factor 配置(pydantic-settings)
├── db.py            SQLAlchemy 2.x 同步引擎 + Session
├── deps.py          鉴权 / admin 装饰器 + 客户端 IP
├── errors.py        ApiError envelope + 错误码(对齐 PRD §6)
├── main.py          Flask 应用工厂
├── security/        JWT / HMAC / password
├── models/          8 张表 ORM(用户/设备/余额/幂等/账单/充值/refresh/审计)
├── schemas/         Pydantic v2 请求/响应模型
├── routers/         Blueprint:auth / billing / account / admin / public
├── services/        auth_service / billing_service / pricing
└── middleware/      request_id / access_log
scripts/
├── migrations/      MySQL 8 版本化 DDL
├── migrate_account_billing.py  幂等 up/down 迁移器
├── db_preflight.py  只读 schema 就绪检查
└── schema.sql       禁止启动期隐式建表的兼容占位文件
tests/
├── test_pricing.py  纯逻辑,无 DB
├── test_hmac.py     凭证签名/验签
└── test_jwt.py      JWT 编/解码
deploy/
└── Dockerfile       gunicorn 4 worker
```

## API 一览

| 模块 | 端点 | 鉴权 | 说明 |
|------|------|------|------|
| 公共 | `GET /healthz` | 无 | 健康检查(DB 探测) |
| 公共 | `GET /metrics` | 无 | Prometheus 指标 |
| 公共 | `GET /openapi.json` | 无 | OpenAPI 3.0 简版 |
| Auth | `POST /v1/auth/register` | 无 | 邮箱密码注册（5/h/IP） |
| Auth | `POST /v1/auth/login` | 无 | 邮箱密码 + 设备登录；失败锁定与双维度限速 |
| Auth | `POST /v1/auth/redeem` | 无 | 兑换 INV/TRY/RCH 码 |
| Auth | `POST /v1/auth/refresh` | 无 | Refresh JWT 单次轮换；旧 token 立即失效 |
| Auth | `POST /v1/auth/logout` | 无 | 幂等撤销 Refresh JWT |
| Auth | `POST /v1/auth/password/reset-request` | 无 | 申请重置；无论邮箱是否存在都返回相同响应 |
| Auth | `POST /v1/auth/password/reset-confirm` | 无 | 30 分钟一次性 token 确认重置 |
| Auth | `POST /v1/auth/password/change` | JWT + Device | 修改密码并撤销全部 Refresh Token |
| Account | `GET /v1/account/me` | JWT | 当前用户 + 余额 |
| Account | `GET /v1/account/bills` | JWT | 账单列表(cursor 分页) |
| Billing | `POST /v1/billing/estimate` | JWT | 费用预估(只读) |
| Billing | `POST /v1/billing/preauth` | JWT + Idempotency-Key | 预占 + pending bill |
| Billing | `POST /v1/billing/settle` | JWT | 结算(差额返还) |
| Billing | `POST /v1/billing/refund` | JWT | 全额退款 |
| 资源 | `POST /v1/resources/bootstrap` | JWT + Device + 有效订阅 | 签发短期数据库下载清单 |
| 资源 | `POST /v1/resources/official-token` | `X-Device-Id`（10 次/小时/IP） | 后端官方账号代登录并返回 HSK / Global Token |
| 资源 | `GET /v1/resources/download/{resourceKey}` | 短期资源票据 | 后端流式转发数据库文件 |
| Admin | `POST /v1/admin/grant` | X-Admin-Token | 手动赠送余额 |
| Admin | `POST /v1/admin/issue-codes` | X-Admin-Token | 批量签发凭证 |
| **Admin 后台 (2026-08-05 M2)** ||||
| 管理 | `POST /admin/login` | 无 | 用户名密码登录 → 颁 HttpOnly cookie |
| 管理 | `POST /admin/logout` | 无 | 清除 cookie |
| 管理 | `GET /admin/me` | Cookie 或 X-Admin-Token | 当前管理员信息 |
| 管理 | `POST /admin/me/change-password` | Cookie | 修改自身密码 |
| 管理 | `GET /admin/health` | 无 | 后台健康检查 |
| 管理 | `GET /v1/admin/users` | Cookie | 用户列表(分页 + 模糊搜索) |
| 管理 | `GET /v1/admin/users/{userId}` | Cookie | 用户详情(含 device 数) |
| 管理 | `POST /v1/admin/users/{userId}/revoke-sessions` | Cookie | 撤销该用户所有 refresh_token(强制下线) |
| 管理 | `POST /v1/admin/users/{userId}/tier` | Cookie | 修改用户 tier / status |
| 管理 | `GET /v1/admin/audit` | Cookie | 审计日志查询(action/actor/targetUser/days 过滤) |
| 管理 | `GET /v1/admin/audit-summary` | Cookie | 按 action group by + 计数(看板) |
| 管理 | `GET /v1/admin/metrics-summary` | Cookie | 看板 KPI:用户总数 / 7 日活跃 / grant 总额 |
| 管理 | `GET /v1/admin/codes/lookup?code=...` | Cookie | 查询某个 RCH/INV/TRY 状态 |
| 管理 | `GET /v1/admin/bills` | Cookie | 账单列表(分页 + status/userId/days 过滤) |
| 管理 | `GET /v1/admin/bills/{billId}` | Cookie | 账单详情(含用户 displayName) |
| 管理 | `GET /v1/admin/export/users.csv` | Cookie | 用户列表 CSV 导出(limit ≤ 10000) |
| 管理 | `GET /v1/admin/export/audit.csv` | Cookie | 审计日志 CSV 导出(days/action/actor/targetUser 过滤) |
| 管理 | `GET /v1/admin/export/codes.csv` | Cookie | 凭证列表 CSV 导出(kind/status 过滤,不含明文 code) |
| 管理 | `GET /v1/admin/export/bills.csv` | Cookie | 账单流水 CSV 导出(status/userId/days 过滤) |

## 与前端对接契约

完整定义见 `docs/backend-prd.md` PRD v2 §5。

**通用约定**:
- 路径前缀 `/v1`
- 鉴权头 `Authorization: Bearer <jwt>` / `X-Device-Id` / `X-Request-Id` / `X-Client-Version`
- 错误响应统一 envelope:
  ```json
  {"error": {"code": "ALREADY_USED", "message": "...", "requestId": "uuid"}}
  ```
- 错误码(PRD §6):`INVALID_CODE / EXPIRED / ALREADY_USED / ALREADY_AUTHENTICATED / NEED_ACTIVATION / INSUFFICIENT_BALANCE / BILL_NOT_FOUND / BILL_ALREADY_SETTLED / BILL_NOT_PENDING / RATE_LIMITED / INTERNAL_ERROR`

## 测试

```bash
pytest                    # 全部
pytest tests/test_pricing.py  # 单文件
pytest --cov=app --cov-fail-under=60
```

## 环境变量

详见 `.env.example`。生产部署前必须替换:
- `LICENSE_SECRET`(HMAC 凭证签名,与客户端共用)
- `JWT_SECRET`(HS256 签名)
- `ADMIN_TOKEN`(运营端 X-Admin-Token,兼容 curl)
- `ADMIN_COOKIE_SECRET`(管理后台 cookie HMAC 签名,至少 32 字节,与管理后台专用)
- `DB_PASSWORD`

受保护资源下载还必须配置：

- `RESOURCE_TICKET_SECRET`：独立的随机签名密钥，不能与 JWT / License 密钥相同。
- `RESOURCE_PUBLIC_BASE_URL`：客户端能够访问的 HTTPS API 根地址。
- `HSK_CORPUS_SOURCE_URL` / `HSK_LOCAL_CORPUS_SOURCE_URL`：仅存在后端的真实源站地址。
- `HSK_CORPUS_SHA256` / `HSK_LOCAL_CORPUS_SHA256`：正式文件 SHA-256。
- `HSK_CORPUS_VERSION` / `HSK_LOCAL_CORPUS_VERSION`：更新文件时同步递增。

`/v1/resources/bootstrap` 会实时校验账号、设备和有效订阅，下载票据默认 180 秒过期。
桌面客户端只能看到 PrismaticaAPI 网关地址，不会收到真实源站 URL。第一阶段仍由后端代理现有源站；上线后还需把对象存储改为私有读取，才能同时关闭历史公开直链。

首次启动引导的“使用官方账号”由 `/v1/resources/official-token` 提供。生产环境需要设置
`OFFICIAL_HSK_USERNAME`、`OFFICIAL_HSK_PASSWORD`、`OFFICIAL_GLOBAL_USERNAME` 和
`OFFICIAL_GLOBAL_PASSWORD`；真实值只保存在后端环境变量中，禁止写入桌面端或提交到仓库。

资源下载权限授予所有未过期的 `active` 订阅。内置计划为 `trial`、`pro_monthly` 和 `team_monthly`；管理员可通过 `POST /v1/admin/users/{userId}/subscriptions` 为尚无有效订阅的用户开通其中一个计划。

## 管理后台登录(2026-08-05 M2)

启动时若 `admin_users` 表为空,自动 seed `root` 账号:

```bash
# 方式一:环境变量指定初始密码(推荐)
export ADMIN_BOOTSTRAP_PASSWORD='S0me-Strong-Pass!'
python -m app.main

# 方式二:不指定 → 自动生成 24 字节随机密码并打印到 stderr(请捕获并立即改)
python -m app.main 2>&1 | tee admin.log
```

种子脚本可单独重跑:`python -m scripts.seed_admin`。

登录后:
- 浏览器:`POST /admin/login` → HttpOnly cookie → 后续走 cookie
- CLI / curl:仍可带 `X-Admin-Token: $ADMIN_TOKEN`(走 fallback 路径)

账户锁定:连续失败 `ADMIN_MAX_FAILED_ATTEMPTS`(默认 5)次后账号被锁,需手动 `admin_users.status` 改回 `active`。

## 命名规范

强制小驼峰优先于 PEP 8:`userName / totalPrice / isActive`;类 UpperCamelCase;常量 `UPPER_SNAKE_CASE`。
