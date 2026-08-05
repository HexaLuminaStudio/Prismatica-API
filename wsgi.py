"""WSGI 入口 —— 仅在 gunicorn / 生产服务器导入。

惰性构造 Flask app,避免 `import wsgi` 时就触发 DB 连接
(测试 / 子脚本不会被这个副作用打到)。
"""
from __future__ import annotations

from app.main import _ensureApp

app = _ensureApp()
