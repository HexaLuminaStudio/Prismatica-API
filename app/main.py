"""Flask 应用工厂(create_app)+ 启动入口。

启动:
    python -m app.main            # 开发
    gunicorn -w 4 -b 0.0.0.0:8000 'app.main:app'   # 生产
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from loguru import logger

from app.config import getSettings
from app.db import initSchemaFromSql, pingDb
from app.errors import registerErrorHandlers
from app.middleware.access_log import installAccessLog
from app.middleware.audit_log import installAuditContext
from app.middleware.request_id import installRequestId

# 限流(默认内存,生产建议 Redis)
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri=getSettings().rateLimitStorageUri,
    # Redis 只用于跨 worker 共享限流计数,不能成为业务接口的单点故障。
    # 存储不可达时自动退回进程内计数；若回退也异常则放行请求并记录告警。
    in_memory_fallback_enabled=True,
    swallow_errors=True,
)


def _parseCorsOrigins(raw: str) -> list[str] | str:
    """解析 CORS 起源列表。

    - 空字符串 → 全部允许(返回特殊字符串 "*",供 flask-cors 跳过白名单检查)
    - 逗号分隔的多项 → 精确白名单
    - 单项 → 同样按列表处理
    """
    parts = [o.strip() for o in raw.split(",") if o.strip()]
    if not parts:
        return "*"
    return parts


def configureLogging() -> None:
    """接管 loguru(结构化 JSON 或可读文本)。"""
    settings = getSettings()
    logger.remove()
    if settings.logJson:
        logger.add(
            lambda msg: print(msg, end=""),
            format="{time:YYYY-MM-DDTHH:mm:ss.SSS} | {level} | {message}",
            level=settings.logLevel,
        )
    else:
        logger.add(
            lambda msg: print(msg, end=""),
            format="<green>{time:HH:mm:ss.SSS}</green> | <level>{level: <7}</level> | {message}",
            level=settings.logLevel,
            colorize=True,
        )


def createApp() -> Flask:
    """Flask 应用工厂。"""
    settings = getSettings()
    configureLogging()

    app = Flask(settings.appName)
    app.config["JSON_AS_ASCII"] = False
    app.config["DEBUG"] = settings.debug

    # 中间件
    installRequestId(app)
    installAccessLog(app)
    installAuditContext(app)

    # CORS(跨域)
    # 允许的请求头:含 Authorization / Idempotency-Key / X-Device-Id / X-Client-Platform
    # 注意:不要传 resources={"/*": ...}——flask-cors 6 会把 "/*" 编译成正则 re.compile("/*"),
    #      该正则匹配任意字符串,意味着 any origin 通过。直接全局注册 CORS 即可。
    # always_send=False:非白名单 origin 不返回 ACAO 头(默认 True 会回退到首项,造成漏洞)。
    corsOrigins = _parseCorsOrigins(settings.corsAllowedOrigins)
    CORS(
        app,
        origins=corsOrigins,
        allow_headers=[
            "Content-Type",
            "Authorization",
            "X-Admin-Token",
            "X-Device-Id",
            "X-Client-Platform",
            "X-Client-Version",
            "Idempotency-Key",
            "X-Request-Id",
            "Cookie",  # 2026-08-05 M2:Admin 后台 cookie 鉴权需要
        ],
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        supports_credentials=settings.corsAllowCredentials,
        max_age=settings.corsMaxAgeSec,
        always_send=False,
    )

    # 限流
    defaultLimit = f"{settings.rateLimitPerMin} per minute"
    limiter.init_app(app)
    limiter.default_limits = [defaultLimit]

    # 路由(2026-08-06 重构:admin 拆为 auth / users / codes / audit / metrics / admins 六个蓝图)
    from app.routers.account import bp as accountBp
    from app.routers.admin_admins import bp as adminAdminsBp
    from app.routers.admin_audit import bp as adminAuditBp
    from app.routers.admin_auth import bp as adminAuthBp
    from app.routers.admin_bills import bp as adminBillsBp
    from app.routers.admin_codes import bp as adminCodesBp
    from app.routers.admin_exports import bp as adminExportsBp
    from app.routers.admin_metrics import bp as adminMetricsBp
    from app.routers.admin_pricing import bp as adminPricingBp
    from app.routers.admin_users import bp as adminUsersBp
    from app.routers.ai import bp as aiBp
    from app.routers.auth import bp as authBp
    from app.routers.billing import bp as billingBp
    from app.routers.public import bp as publicBp
    from app.routers.resources import bp as resourcesBp

    app.register_blueprint(authBp)
    app.register_blueprint(accountBp)
    app.register_blueprint(aiBp)
    app.register_blueprint(billingBp)
    app.register_blueprint(adminAuthBp)
    app.register_blueprint(adminUsersBp)
    app.register_blueprint(adminCodesBp)
    app.register_blueprint(adminBillsBp)
    app.register_blueprint(adminExportsBp)
    app.register_blueprint(adminAuditBp)
    app.register_blueprint(adminMetricsBp)
    app.register_blueprint(adminPricingBp)
    app.register_blueprint(adminAdminsBp)
    app.register_blueprint(publicBp)
    app.register_blueprint(resourcesBp)

    # 错误处理
    registerErrorHandlers(app)

    # 启动期:探测 DB;autoInitSchema 时执行 schema.sql
    if not pingDb():
        logger.warning("[Startup] DB 不可达,跳过 schema 初始化")
    elif settings.autoInitSchema:
        schemaPath = Path(__file__).resolve().parent.parent / "scripts" / "schema.sql"
        if schemaPath.exists():
            try:
                initSchemaFromSql(str(schemaPath))
            except Exception as e:
                logger.exception(f"[Startup] schema 初始化失败: {e}")

        # 2026-08-05 M2:启动期种子 admin 账号(管理员后台登录用)
        # 失败仅日志,不阻断启动;运营可后续手动跑 scripts/seed_admin.py。
        try:
            from scripts.seed_admin import _ensureRootAdmin

            _ensureRootAdmin()
        except Exception as e:
            logger.exception(f"[Startup] seed_admin 失败: {e}")

    logger.info(f"[Startup] {settings.appName} ready env={settings.env} db={settings.dbHost}")
    return app


# WSGI 入口(gunicorn: 'app.main:app')—— 仅在作为入口脚本时构造,
# 避免被其他模块(测试 / 子脚本)import 时副作用触发 DB 连接。
app = None


def _ensureApp() -> Flask:
    """惰性构造:测试可通过环境变量跳过 DB 初始化。"""
    global app
    if app is None:
        app = createApp()
    return app


if __name__ == "__main__":
    settings = getSettings()
    _ensureApp().run(host=settings.host, port=settings.port, debug=settings.debug)
