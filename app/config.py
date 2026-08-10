"""12-factor 配置(从环境变量读取)。

生产环境必须通过环境变量注入:
    - DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD
    - LICENSE_SECRET (HMAC 凭证签名,与客户端 signed_code.py 共用)
    - JWT_SECRET     (HS256 签名密钥)
    - ADMIN_TOKEN    (运营端 X-Admin-Token 校验)
    - REDIS_URL      (限流 / Session)

字段命名:沿用项目小驼峰规范(`dbHost`),通过 `alias` 映射到
    环境变量名 `DB_HOST` / `DB_PORT` 等标准大写下划线写法。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    """全局配置"""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # ---- 应用 ----
    appName: str = Field(default="prismatica-backend", alias="APP_NAME")
    appVersion: str = Field(default="2026.08.07-p0b", alias="APP_VERSION")
    buildId: str = Field(default="local", alias="BUILD_ID")
    gitCommit: str = Field(default="unknown", alias="GIT_COMMIT")
    env: str = Field(default="dev", alias="ENV")
    debug: bool = Field(default=False, alias="DEBUG")
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")

    # ---- MySQL ----
    dbHost: str = Field(default="127.0.0.1", alias="DB_HOST")
    dbPort: int = Field(default=3306, alias="DB_PORT")
    dbName: str = Field(default="prismatica", alias="DB_NAME")
    dbUser: str = Field(default="prismatica", alias="DB_USER")
    dbPassword: str = Field(default="prismatica", alias="DB_PASSWORD")
    dbPoolSize: int = Field(default=10, alias="DB_POOL_SIZE")
    dbMaxOverflow: int = Field(default=20, alias="DB_MAX_OVERFLOW")
    dbPoolRecycleSec: int = Field(default=1800, alias="DB_POOL_RECYCLE_SEC")

    @property
    def dbUrl(self) -> str:
        """SQLAlchemy 同步 URL(PyMySQL 驱动)。"""
        return (
            f"mysql+pymysql://{self.dbUser}:{self.dbPassword}"
            f"@{self.dbHost}:{self.dbPort}/{self.dbName}"
            "?charset=utf8mb4"
        )

    # ---- Redis ----
    redisUrl: str = Field(default="redis://127.0.0.1:6379/0", alias="REDIS_URL")
    rateLimitStorageUri: str = Field(
        default="memory://", alias="RATELIMIT_STORAGE_URI"
    )

    # ---- HMAC / JWT ----
    licenseSecret: str = Field(
        default="DEV-LICENSE-HMAC-SECRET-PLEASE-OVERRIDE-IN-PROD",
        alias="LICENSE_SECRET",
        description="HMAC-SHA256 共享密钥,与客户端 signed_code.py 一致",
    )
    jwtSecret: str = Field(
        default="DEV-JWT-HS256-SECRET-PLEASE-OVERRIDE-IN-PROD",
        alias="JWT_SECRET",
        description="JWT HS256 签名密钥(必须与 LICENSE_SECRET 不同)",
    )
    jwtAlg: str = Field(default="HS256", alias="JWT_ALG")
    jwtIssuer: str = Field(default="prismatica-api", alias="JWT_ISSUER")
    jwtAudience: str = Field(default="prismatica-client", alias="JWT_AUDIENCE")
    jwtAccessTtlSec: int = Field(default=3600, alias="JWT_ACCESS_TTL")
    jwtRefreshTtlSec: int = Field(default=2592000, alias="JWT_REFRESH_TTL")

    # ---- 受保护资源下载 ----
    resourceTicketSecret: str = Field(
        default="DEV-RESOURCE-TICKET-SECRET-PLEASE-OVERRIDE-IN-PROD",
        alias="RESOURCE_TICKET_SECRET",
        description="短期资源下载票据签名密钥，必须与 JWT_SECRET 不同",
    )
    resourceTicketTtlSec: int = Field(
        default=180,
        ge=60,
        le=900,
        alias="RESOURCE_TICKET_TTL_SEC",
    )
    resourcePublicBaseUrl: str = Field(
        default="",
        alias="RESOURCE_PUBLIC_BASE_URL",
        description="外部可访问的 API 根地址；留空时根据当前请求生成",
    )
    resourceUpstreamConnectTimeoutSec: int = Field(
        default=10,
        ge=1,
        le=60,
        alias="RESOURCE_UPSTREAM_CONNECT_TIMEOUT_SEC",
    )
    resourceUpstreamReadTimeoutSec: int = Field(
        default=60,
        ge=10,
        le=600,
        alias="RESOURCE_UPSTREAM_READ_TIMEOUT_SEC",
    )
    hskCorpusSourceUrl: str = Field(
        default="",
        alias="HSK_CORPUS_SOURCE_URL",
    )
    hskCorpusSha256: str = Field(
        default="",
        alias="HSK_CORPUS_SHA256",
    )
    hskCorpusVersion: str = Field(default="1", alias="HSK_CORPUS_VERSION")
    hskLocalCorpusSourceUrl: str = Field(
        default="",
        alias="HSK_LOCAL_CORPUS_SOURCE_URL",
    )
    hskLocalCorpusSha256: str = Field(
        default="",
        alias="HSK_LOCAL_CORPUS_SHA256",
    )
    hskLocalCorpusVersion: str = Field(
        default="1",
        alias="HSK_LOCAL_CORPUS_VERSION",
    )

    # ---- Admin ----
    adminToken: str = Field(
        default="DEV-ADMIN-TOKEN-PLEASE-OVERRIDE-IN-PROD",
        alias="ADMIN_TOKEN",
        description="运营端 X-Admin-Token 校验值",
    )
    adminCookieSecret: str = Field(
        default="DEV-ADMIN-COOKIE-SECRET-PLEASE-OVERRIDE-IN-PROD",
        alias="ADMIN_COOKIE_SECRET",
        description="(2026-08-05 M2) cookie 签名 HMAC 密钥,至少 32 字节",
    )
    adminCookieMaxAgeSec: int = Field(
        default=7200,
        alias="ADMIN_COOKIE_MAX_AGE",
        description="(2026-08-05 M2) admin 会话 cookie 有效期(秒),默认 2h",
    )
    adminBootstrapPassword: str = Field(
        default="",
        alias="ADMIN_BOOTSTRAP_PASSWORD",
        description="(2026-08-05 M2) 启动期种子 root 账号的初始密码;空字符串表示不启用种子",
    )
    adminMaxFailedAttempts: int = Field(
        default=5,
        alias="ADMIN_MAX_FAILED_ATTEMPTS",
        description="(2026-08-05 M2) 连续登录失败 N 次后锁定账号",
    )
    adminCookieSecure: bool = Field(
        default=False,
        alias="ADMIN_COOKIE_SECURE",
        description=(
            "(2026-08-06 M2 cors-fix) cookie Secure 标记;"
            "HTTPS 或跨端口(需 SameSite=None)部署时设 true"
        ),
    )

    # ---- 限流 ----
    rateLimitPerMin: int = Field(default=60, alias="RATE_LIMIT_PER_MIN")

    # ---- 日志 ----
    logLevel: str = Field(default="INFO", alias="LOG_LEVEL")
    logJson: bool = Field(default=False, alias="LOG_JSON")

    # ---- CORS(跨域)----
    # 逗号分隔的 origin 白名单,留空表示允许所有(仅 dev)。
    # 生产必须显式列出,例如:https://app.example.com,https://admin.example.com
    corsAllowedOrigins: str = Field(default="", alias="CORS_ALLOWED_ORIGINS")
    corsAllowCredentials: bool = Field(default=False, alias="CORS_ALLOW_CREDENTIALS")
    corsMaxAgeSec: int = Field(default=600, alias="CORS_MAX_AGE_SEC")

    # ---- 自动建表(开发期便利)----
    autoInitSchema: bool = Field(default=True, alias="AUTO_INIT_SCHEMA")


@lru_cache(maxsize=1)
def _settingsCache() -> Settings:
    return Settings()


def getSettings() -> Settings:
    """获取全局配置单例(只读一次 .env)。"""
    return _settingsCache()


def resetSettingsCache() -> None:
    """测试钩子:清空缓存,下次 getSettings() 重新读 env。"""
    _settingsCache.cache_clear()
