# Prismatica Backend

Prismatica 桌面客户端的云端后端(对齐 `docs/backend-prd.md` PRD v2)。

- **范围**:用户登录 + 积分结算(redeem / refresh / estimate / preauth / settle / refund / account / admin)
- **技术栈**:Flask 3 + SQLAlchemy 2.x(同步)+ PyMySQL + PyJWT + bcrypt + pydantic v2 + loguru + Flask-Limiter + prometheus-client
- **持久化**:MySQL 8.0(`scripts/schema.sql` 一键建库,8 张表)
- **部署**:Docker + docker-compose(Flask + MySQL + Redis)

## 快速启动

### 方式一:本地直接跑

```bash
# 1. 装依赖(uv 推荐)
uv sync
# 或 pip install -e .[dev]

# 2. 准备 MySQL,导入 schema
mysql -uroot -p < scripts/schema.sql

# 3. 复制并修改 .env
cp .env.example .env
# 填入 LICENSE_SECRET / JWT_SECRET / ADMIN_TOKEN / DB_PASSWORD

# 4. 启动
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
└── schema.sql       MySQL 8 表结构 + 索引 + CHECK 约束
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
| Auth | `POST /v1/auth/redeem` | 无 | 兑换 INV/TRY/RCH 码 |
| Auth | `POST /v1/auth/refresh` | 无 | refresh_token 换新 access |
| Auth | `POST /v1/auth/logout` | 可选 JWT | 撤销 refresh_token |
| Account | `GET /v1/account/me` | JWT | 当前用户 + 余额 |
| Account | `GET /v1/account/bills` | JWT | 账单列表(cursor 分页) |
| Billing | `POST /v1/billing/estimate` | JWT | 费用预估(只读) |
| Billing | `POST /v1/billing/preauth` | JWT + Idempotency-Key | 预占 + pending bill |
| Billing | `POST /v1/billing/settle` | JWT | 结算(差额返还) |
| Billing | `POST /v1/billing/refund` | JWT | 全额退款 |
| Admin | `POST /v1/admin/grant` | X-Admin-Token | 手动赠送余额 |
| Admin | `POST /v1/admin/issue-codes` | X-Admin-Token | 批量签发凭证 |

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
- `ADMIN_TOKEN`(运营端 X-Admin-Token)
- `DB_PASSWORD`

## 命名规范

强制小驼峰优先于 PEP 8:`userName / totalPrice / isActive`;类 UpperCamelCase;常量 `UPPER_SNAKE_CASE`。