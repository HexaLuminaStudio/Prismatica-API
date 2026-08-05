"""Flask 应用工厂(create_app)+ 启动入口。

启动:
    python -m app.main            # 开发
    gunicorn -w 4 -b 0.0.0.0:8000 'app.main:app'   # 生产
"""
from __future__ import annotations

from pathlib import Path

from flask import Flask
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from loguru import logger

from app.config import getSettings
from app.db import initSchemaFromSql, pingDb
from app.errors import registerErrorHandlers
from app.middleware.access_log import installAccessLog
from app.middleware.request_id import installRequestId

# 限流(默认内存,生产建议 Redis)
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri=getSettings().rateLimitStorageUri,
)


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

    # 限流
    defaultLimit = f"{settings.rateLimitPerMin} per minute"
    limiter.init_app(app)
    limiter.default_limits = [defaultLimit]

    # 路由
    from app.routers.account import bp as accountBp
    from app.routers.admin import bp as adminBp
    from app.routers.auth import bp as authBp
    from app.routers.billing import bp as billingBp
    from app.routers.public import bp as publicBp

    app.register_blueprint(authBp)
    app.register_blueprint(accountBp)
    app.register_blueprint(billingBp)
    app.register_blueprint(adminBp)
    app.register_blueprint(publicBp)

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

    logger.info(
        f"[Startup] {settings.appName} ready env={settings.env} db={settings.dbHost}"
    )
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
