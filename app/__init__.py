# coding: utf-8
"""Prismatica 云端后端(Flask + MySQL)

按 PRD v2(用户登录 + 积分结算)实施,替代 PRD v1 的 Flask + 12 张表方案。
本目录分层:
    app/
    ├── config.py        12-factor 配置(pydantic-settings)
    ├── db.py            SQLAlchemy 2.x 同步引擎 + Session
    ├── security/        JWT / HMAC / 密码(预留)
    ├── models/          ORM 模型(8 张表)
    ├── schemas/         Pydantic v2 请求/响应模型
    ├── routers/         Flask Blueprint 路由层
    ├── services/        业务服务(auth_service / billing_service / pricing)
    ├── middleware/      RequestId / access_log
    └── errors.py        ApiError envelope + 错误码
"""