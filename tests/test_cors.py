"""CORS 中间件验证。"""

from __future__ import annotations

import os

import pytest

# 在 import app.main 之前注入 dummy env,避免触发 DB 连接
os.environ.setdefault("AUTO_INIT_SCHEMA", "false")
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "3306")
os.environ.setdefault("LICENSE_SECRET", "x" * 32)
os.environ.setdefault("JWT_SECRET", "y" * 32)
os.environ.setdefault("ADMIN_TOKEN", "z" * 32)


@pytest.fixture
def client(monkeypatch):
    """构造一个显式开了 CORS 白名单的 Flask 测试客户端。

    注意:不要用 resetSettingsCache()——它会冲掉其他测试模块对
    getSettings() 的副作用(如 jwtAccessTtlSec=1 的过期测试)。
    这里直接 monkeypatch CORS 需要的字段即可。
    """
    from app.config import getSettings
    from app.main import createApp

    settings = getSettings()
    monkeypatch.setattr(settings, "corsAllowedOrigins", "https://app.example.com,https://admin.example.com")
    monkeypatch.setattr(settings, "corsAllowCredentials", False)
    monkeypatch.setattr(settings, "corsMaxAgeSec", 600)

    app = createApp()
    app.config["TESTING"] = True
    return app.test_client()


def testCorsPreflightAllowedOrigin(client):
    """白名单内 origin 的 OPTIONS 预检应返回 200 + 关键 CORS 响应头。"""
    res = client.options(
        "/v1/auth/redeem",
        headers={
            "Origin": "https://app.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type,Authorization",
        },
    )
    assert res.status_code in (200, 204)
    assert res.headers.get("Access-Control-Allow-Origin") == "https://app.example.com"
    allowHeaders = res.headers.get("Access-Control-Allow-Headers", "")
    assert "Authorization" in allowHeaders
    assert "Content-Type" in allowHeaders


def testCorsPreflightRejectsUnknownOrigin(client):
    """非白名单 origin 应不返回自己的 Access-Control-Allow-Origin。"""
    res = client.options(
        "/v1/auth/redeem",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    # flask-cors 不会给未授权 origin 回 ACAO 头
    assert res.headers.get("Access-Control-Allow-Origin") != "https://evil.example.com"


def testParseCorsOriginsHelper():
    """直接测 _parseCorsOrigins 工具。"""
    from app.main import _parseCorsOrigins

    assert _parseCorsOrigins("") == "*"
    assert _parseCorsOrigins("https://a.com") == ["https://a.com"]
    assert _parseCorsOrigins("https://a.com, https://b.com ,") == [
        "https://a.com",
        "https://b.com",
    ]


def testCors_realRequestHeadersOnAllowedOrigin(client):
    """真实 GET /healthz(白名单 origin)应附带 CORS 头。

    /healthz 之前会因为 DB 探测失败返回 503,但 CORS 头照样该在。
    """
    res = client.get(
        "/healthz",
        headers={"Origin": "https://app.example.com"},
    )
    # DB 不可达 → 503,只要 CORS 头还在就算通过
    assert res.headers.get("Access-Control-Allow-Origin") == "https://app.example.com"


def testCors_credentialsModeWithCrossPortOrigin(monkeypatch):
    """(2026-08-06 M2 cors-fix)凭据模式 + 跨端口 origin 预检。

    模拟生产部署:前端 http://103.236.55.211:8080,后端 :8000,
    supports_credentials=True。预检 OPTIONS 必须:
      - 回显该 origin
      - 显式返回 Access-Control-Allow-Credentials: true
      - 不返回 "*"(浏览器会拒绝带凭证的 "*")
    """
    from app.config import getSettings
    from app.main import createApp

    settings = getSettings()
    monkeypatch.setattr(
        settings,
        "corsAllowedOrigins",
        "http://103.236.55.211:8080",
    )
    monkeypatch.setattr(settings, "corsAllowCredentials", True)
    monkeypatch.setattr(settings, "corsMaxAgeSec", 600)

    app = createApp()
    app.config["TESTING"] = True
    testClient = app.test_client()

    res = testClient.options(
        "/v1/admin/auth/login",
        headers={
            "Origin": "http://103.236.55.211:8080",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert res.status_code in (200, 204)
    assert res.headers.get("Access-Control-Allow-Origin") == "http://103.236.55.211:8080"
    # 凭据模式关键头
    assert res.headers.get("Access-Control-Allow-Credentials") == "true"
    # 不应是 "*"
    assert res.headers.get("Access-Control-Allow-Origin") != "*"


def testCors_originNotInAllowlistNotCredentialed(monkeypatch):
    """(2026-08-06 M2 cors-fix)凭据模式下,恶意 origin 不应回 ACAO。"""
    from app.config import getSettings
    from app.main import createApp

    settings = getSettings()
    monkeypatch.setattr(
        settings,
        "corsAllowedOrigins",
        "http://103.236.55.211:8080",
    )
    monkeypatch.setattr(settings, "corsAllowCredentials", True)

    app = createApp()
    app.config["TESTING"] = True
    testClient = app.test_client()

    res = testClient.options(
        "/v1/admin/auth/login",
        headers={
            "Origin": "http://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    # 不应给未授权 origin 回显自己的 ACAO
    acao = res.headers.get("Access-Control-Allow-Origin")
    assert acao != "http://evil.example.com"
