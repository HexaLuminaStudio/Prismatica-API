"""/v1/admin/export/* 路由(2026-08-06 新增):CSV 导出。

端点(均需 cookie 鉴权):
    GET /v1/admin/export/users.csv     用户列表(limit ≤ 10000)
    GET /v1/admin/export/audit.csv     审计日志(days / action / actor / targetUser 过滤)
    GET /v1/admin/export/codes.csv     凭证列表(kind / status 过滤)
    GET /v1/admin/export/bills.csv     账单流水(status / userId / days 过滤)

返回 text/csv attachment(UTF-8 BOM,Excel 直接打开不乱码)。
"""
from __future__ import annotations

import csv
import io
from typing import Any

from flask import Blueprint, Response, request

from app.deps import requireAdminCookie
from app.errors import ApiError
from app.services.admin_export_service import (
    exportAudit,
    exportBills,
    exportCodes,
    exportUsers,
)

bp = Blueprint("admin_exports", __name__, url_prefix="/v1/admin/export")

_MAX_LIMIT = 10000


def _limit() -> int:
    """解析 limit(默认 5000,上限 10000)。"""
    raw = request.args.get("limit", "5000")
    try:
        value = int(raw)
    except ValueError as e:
        raise ApiError("BAD_REQUEST", f"limit 格式错误: {e}") from e
    return max(1, min(_MAX_LIMIT, value))


def _csvResponse(rows: list[dict[str, Any]], filename: str) -> Response:
    """行字典 → CSV 响应(UTF-8 BOM + attachment)。"""
    fieldnames = list(rows[0].keys()) if rows else []
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    body = "\ufeff" + buf.getvalue()  # BOM:Excel 识别 UTF-8
    resp = Response(body, mimetype="text/csv; charset=utf-8")
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


@bp.get("/users.csv")
@requireAdminCookie
def exportUsersRoute():
    """用户列表导出。"""
    rows = exportUsers(limit=_limit())
    return _csvResponse(rows, "users.csv")


@bp.get("/audit.csv")
@requireAdminCookie
def exportAuditRoute():
    """审计日志导出(过滤同列表页)。"""
    daysRaw = request.args.get("days")
    try:
        days = int(daysRaw) if daysRaw else None
    except ValueError as e:
        raise ApiError("BAD_REQUEST", f"days 格式错误: {e}") from e
    rows = exportAudit(
        limit=_limit(),
        days=days,
        action=(request.args.get("action") or "").strip() or None,
        actor=(request.args.get("actor") or "").strip() or None,
        targetUser=(request.args.get("targetUser") or "").strip() or None,
    )
    return _csvResponse(rows, "audit.csv")


@bp.get("/codes.csv")
@requireAdminCookie
def exportCodesRoute():
    """凭证列表导出(不含明文 code)。"""
    rows = exportCodes(
        limit=_limit(),
        kind=(request.args.get("kind") or "").strip() or None,
        status=(request.args.get("status") or "").strip() or None,
    )
    return _csvResponse(rows, "codes.csv")


@bp.get("/bills.csv")
@requireAdminCookie
def exportBillsRoute():
    """账单流水导出。"""
    daysRaw = request.args.get("days")
    try:
        days = int(daysRaw) if daysRaw else None
    except ValueError as e:
        raise ApiError("BAD_REQUEST", f"days 格式错误: {e}") from e
    rows = exportBills(
        limit=_limit(),
        status=(request.args.get("status") or "").strip() or None,
        userId=(request.args.get("userId") or "").strip() or None,
        days=days,
    )
    return _csvResponse(rows, "bills.csv")


__all__ = ["bp"]
