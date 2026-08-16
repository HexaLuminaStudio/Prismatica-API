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
python -m scripts.migrate_dynamic_pricing
python -m scripts.migrate_corpus_download_pricing
python -m scripts.db_preflight

# 4. 启动（P0-A M2-M9 完成前，旧 ORM 尚未适配新 schema）
python -m app.main
# 或生产模式
gunicorn -w 4 -b 0.0.0.0:8000 'app.main:app'
```

### 方式二:Docker Compose

```bash
cd PrismaticaAPI
docker compose up -d --build
# 三个后端实例分别只监听宿主机回环地址 8100 / 8101 / 8102。
curl http://127.0.0.1:8100/healthz
curl http://127.0.0.1:8101/healthz
curl http://127.0.0.1:8102/healthz
```

Linux 上 Compose 通过 `extra_hosts: host-gateway` 把 `host.docker.internal`
解析到宿主机网关。宝塔 MySQL 需要允许 Docker bridge 网段访问，但服务器
防火墙不应向公网开放 3306。数据库连接使用显式连接、读写和连接池等待超时，
避免 MySQL 异常时长期占用 Gunicorn 请求槽。

生产环境使用单公网入口隔离请求槽位：

- `api`：`127.0.0.1:8100`，处理登录、余额、流水、Admin 等短请求。
- `ai`：`127.0.0.1:8101`，处理 `/v1/ai/` 下的 SSE 长连接。
- `resources`：`127.0.0.1:8102`，处理 `/v1/resources/download/` 流式下载。
- 宝塔 Nginx：继续监听公网 `8000`，按路径转发到上述三个内部实例。

完整 Nginx `server` 配置见 `deploy/nginx-prismatica-api.conf`。三个内部端口
必须绑定 `127.0.0.1`，不得加入云安全组或宝塔公网放行。启用 Nginx 前先运行
`docker compose config --quiet`，启动三个服务并逐一检查内部健康接口；随后执行
`nginx -t`，确认无误后再重载 Nginx。Qt、Admin 和官网仍使用原公网 IP 与
`8000`，客户端地址无需改变。

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
├── Dockerfile                      后端镜像
└── nginx-prismatica-api.conf       宝塔 Nginx 单入口分流配置
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
| Billing | `POST /v1/billing/settle` | JWT | 兼容入口；受控账单拒绝客户端自行申报费用 |
| Billing | `POST /v1/billing/commit-fixed` | JWT | 固定价任务按预授权价格快照结算 |
| Billing | `POST /v1/billing/commit-metered` | JWT | 按量任务按预授权资源量与价格快照结算 |
| Billing | `POST /v1/billing/refund` | JWT | 全额退款 |
| 定价 | `GET /v1/pricing/catalog` | 无 | 当前生效价格状态、来源、生效时间与规则（客户端约 30 秒刷新） |
| AI | `POST /v1/ai/chat` | JWT + Idempotency-Key | 平台密钥代理调用，按真实输入/输出 Token 结算 |
| AI | `POST /v1/ai/chat/stream` | JWT + Idempotency-Key | SSE 返回阶段进度与正文增量，最终按供应商 usage 结算 |
| 资源 | `POST /v1/resources/bootstrap` | JWT + Device | 为所有有效账号签发免费数据库下载清单 |
| 资源 | `POST /v1/resources/official-token` | `X-Device-Id`（10 次/小时/IP） | 后端官方账号代登录并返回 HSK / Global Token |
| 资源 | `GET /v1/resources/download/{resourceKey}` | 短期资源票据 | 后端流式转发数据库文件 |
| Admin | `POST /v1/admin/grant` | X-Admin-Token | 手动赠送余额 |
| Admin | `POST /v1/admin/issue-codes` | X-Admin-Token | 批量签发凭证 |
| **Admin 后台 (2026-08-05 M2)** ||||
| 管理 | `POST /admin/login` | 无 | 用户名密码登录 → 颁 HttpOnly cookie |
| 管理 | `POST /admin/logout` | 无 | 清除 cookie |
| 管理 | `GET /admin/me` | Cookie 或 X-Admin-Token | 当前管理员信息 |
| 管理 | `POST /admin/me/change-password` | Cookie | 修改自身密码 |
| 管理 | `GET /v1/admin/pricing` | Cookie | 查看当前价格与发布历史 |
| 管理 | `POST /v1/admin/pricing/drafts` | owner Cookie | 创建完整价格草稿 |
| 管理 | `POST /v1/admin/pricing/{versionCode}/publish` | owner Cookie | 发布新价格，新请求立即生效 |
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
- `ENV=prod`，并设置可追踪的 `BUILD_ID` / `GIT_COMMIT`
- `AUTO_INIT_SCHEMA=false`（Compose 也会强制覆盖，禁止多实例启动期隐式建表）
- `LICENSE_SECRET`(HMAC 凭证签名,与客户端共用)
- `JWT_SECRET`(HS256 签名)
- `ADMIN_TOKEN`(运营端 X-Admin-Token,兼容 curl)
- `ADMIN_COOKIE_SECRET`(管理后台 cookie HMAC 签名,至少 32 字节,与管理后台专用)
- `DB_PASSWORD`
- `AI_API_KEY`（平台统一持有的供应商密钥，禁止下发到客户端）

平台 AI 还可通过 `AI_BASE_URL`、`AI_MODEL_CHAT`、`AI_MAX_OUTPUT_TOKENS` 调整供应商与模型。
供应商响应必须包含可核验的 Token usage；缺少 usage 时请求失败并释放预授权，不进行估算扣费。
流式端点依次返回 `progress`、`delta`、`completed` 或 `error` 事件，并透传 `heartbeat`
保持长连接；客户端断开、上游失败或最终 usage 缺失时自动释放预授权。

受保护资源下载还必须配置：

- `RESOURCE_TICKET_SECRET`：独立的随机签名密钥，不能与 JWT / License 密钥相同。
- `RESOURCE_PUBLIC_BASE_URL`：客户端能够访问的 HTTPS API 根地址。
- `HSK_CORPUS_SOURCE_URL` / `HSK_LOCAL_CORPUS_SOURCE_URL`：仅存在后端的真实源站地址。
- `HSK_CORPUS_SHA256` / `HSK_LOCAL_CORPUS_SHA256`：正式文件 SHA-256。
- `HSK_CORPUS_VERSION` / `HSK_LOCAL_CORPUS_VERSION`：更新文件时同步递增。

`/v1/resources/bootstrap` 会实时校验账号和设备，所有有效登录用户都可下载数据库文件，下载票据默认 180 秒过期。
桌面客户端只能看到 PrismaticaAPI 网关地址，不会收到真实源站 URL。第一阶段仍由后端代理现有源站；上线后还需把对象存储改为私有读取，才能同时关闭历史公开直链。

首次启动引导的“使用官方账号”由 `/v1/resources/official-token` 提供。生产环境需要设置
`OFFICIAL_HSK_USERNAME`、`OFFICIAL_HSK_PASSWORD`、`OFFICIAL_GLOBAL_USERNAME` 和
`OFFICIAL_GLOBAL_PASSWORD`；真实值只保存在后端环境变量中，禁止写入桌面端或提交到仓库。

启动所需的 HSK 数据库文件面向所有有效登录用户免费提供，不要求试用或订阅权限。HSK / Global 检索结果的单项与批量下载继续按当前价格目录报价、预占和结算；失败或取消会释放预占。内置订阅计划仍可用于其他产品权益，管理员可通过 `POST /v1/admin/users/{userId}/subscriptions` 管理用户订阅。

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
