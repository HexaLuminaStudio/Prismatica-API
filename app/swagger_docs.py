"""Flasgger Swagger 文档配置。

提供:
- /apidocs/             Swagger UI 交互式文档(flasgger 自带本地静态资源,不依赖外网 CDN)
- /apispec_1.json       Flasgger 生成的 OpenAPI 3 规范
- /openapi.json         复用同一份规范(兼容既有部署脚本与测试契约)

接口文档编写约定:在路由函数 docstring 中,用 --- 分隔 YAML 段;
第一行为摘要、中间为详细说明、--- 之后为 OpenAPI 3 定义(参考 flasgger 文档)。
"""

from __future__ import annotations

import re

from flask import current_app
from flasgger import Swagger

# 纳入文档的路径前缀(与旧 /openapi.json 行为一致,排除基础设施端点)
_DOC_PREFIXES = ("/v1/", "/admin/")

# 明确排除的基础设施端点
_EXCLUDED_PATHS = ("/healthz", "/openapi.json", "/metrics", "/admin/health")

_HTTP_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH")

# OpenAPI 3 鉴权方案
_SECURITY_SCHEMES = {
    "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
    "adminCookie": {"type": "apiKey", "in": "cookie", "name": "session"},
    "adminToken": {"type": "apiKey", "in": "header", "name": "X-Admin-Token"},
}

# 文档分组(tag)
_TAGS = [
    {"name": "auth", "description": "身份认证(注册 / 登录 / 兑换 / 密码)"},
    {"name": "account", "description": "账户(个人信息 / 账单 / 设备 / 订阅)"},
    {"name": "billing", "description": "计费(预估 / 预占 / 结算 / 退款)"},
    {"name": "pricing", "description": "定价目录"},
    {"name": "ai", "description": "平台 AI(代理调用,按 Token 结算)"},
    {"name": "resources", "description": "语料资源(清单 / 下载)"},
    {"name": "admin", "description": "运营管理后台"},
]

_swagger: Swagger | None = None


def _ruleFilter(rule) -> bool:
    """只保留业务路径(排除基础设施与 flasgger 自身端点)。"""
    if rule.endpoint == "static" or rule.endpoint.startswith("flasgger"):
        return False
    path = rule.rule
    if path in _EXCLUDED_PATHS:
        return False
    return path.startswith(_DOC_PREFIXES)


def _toSwaggerPath(rule: str) -> str:
    """把 Flask 路由参数 <string:userId> 转成 OpenAPI 的 {userId}。"""
    for arg in re.findall(r"(<([^<>]*:)?([^<>]*)>)", rule):
        rule = rule.replace(arg[0], "{%s}" % arg[2])
    return rule


def buildSwaggerTemplate() -> dict:
    """构造 OpenAPI 3 模板(info / tags / servers)。"""
    return {
        "info": {
            "title": "Prismatica 云端后端 API",
            "description": (
                "Prismatica 桌面客户端的云端后端(对齐 PRD v2)。\n\n"
                "统一响应 envelope:\n"
                '- 成功:{"code": "OK", "data": {...}, "requestId": "..."}\n'
                '- 失败:{"code": "<ERROR_CODE>", "message": "...", "requestId": "..."}\n\n'
                "鉴权:\n"
                "- 用户端:Authorization: Bearer <JWT>(即下方 bearerAuth)\n"
                "- Admin 后台:登录后 HttpOnly cookie,或 X-Admin-Token 直通"
            ),
            "version": "0.1.0",
        },
        "tags": _TAGS,
        "servers": [{"url": "/"}],
    }


def registerSwaggerDocs(app) -> Swagger:
    """初始化 Flasgger,注册 /apidocs/ 与 /apispec_1.json。"""
    global _swagger
    _swagger = Swagger(
        app,
        config={
            "openapi": "3.0.3",
            "specs": [
                {
                    "endpoint": "apispec_1",
                    "route": "/apispec_1.json",
                    "rule_filter": _ruleFilter,
                }
            ],
            "swagger_ui": True,
            "specs_route": "/apidocs/",
        },
        template=buildSwaggerTemplate(),
        merge=True,
    )
    return _swagger


def getOpenApiSpec() -> dict:
    """返回 Flasgger 生成的 OpenAPI 3 规范(与 /apidocs/ 同源)。

    兼容旧 /openapi.json 的"全量路径"契约:
    - 未写 swagger YAML 的路由也以默认 operation 形式出现
    - 注入 components.securitySchemes(OpenAPI 3 鉴权定义)
    """
    if _swagger is None:
        raise RuntimeError("registerSwaggerDocs 尚未初始化")
    spec = _swagger.get_apispecs("apispec_1")
    spec.setdefault("components", {})["securitySchemes"] = _SECURITY_SCHEMES
    paths = spec["paths"]
    for rule in current_app.url_map.iter_rules():
        if not _ruleFilter(rule):
            continue
        path = _toSwaggerPath(rule.rule)
        for method in sorted(m for m in rule.methods if m in _HTTP_METHODS):
            entry = paths.setdefault(path, {})
            if method.lower() not in entry:
                entry[method.lower()] = {
                    "summary": rule.endpoint,
                    "responses": {"200": {"description": "OK"}},
                }
    return spec
