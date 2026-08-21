"""/v1/admin/codes/* 路由(2026-08-06 重构)。

端点:
    POST  /v1/admin/codes                  批量签发(立即持久化)
    GET   /v1/admin/codes                  凭证列表(不含明文 code)
    GET   /v1/admin/codes/lookup           按明文 code 查状态
    POST  /v1/admin/codes/{codeHash}/revoke 撤销某凭证

业务由 admin_code_service / admin_audit_service 承载。
"""

from __future__ import annotations

from flask import Blueprint, g, request
from pydantic import ValidationError

from app.deps import requireAdminCookie
from app.errors import ApiError, successEnvelope
from app.schemas.admin import (
    AdminCodeListItem,
    AdminCodeListResponse,
    AdminCodeLookupResponse,
    AdminCodeRevokeResponse,
    AdminIssueCodesRequest,
    AdminIssueCodesResponse,
    AdminIssuedCodeItem,
)
from app.services.admin_audit_service import listCodes, lookupCode, revokeCode
from app.services.admin_code_service import issueCodes

bp = Blueprint("admin_codes", __name__, url_prefix="/v1/admin/codes")


def _actor() -> str:
    """拿当前管理员 username(供 audit_logs.actor)。"""
    return getattr(g, "adminUsername", None) or getattr(g, "adminActor", "admin")


@bp.post("")
@requireAdminCookie
def postIssueCodes():
    """批量签发凭证,立即持久化到 license_codes 表。

    ---
    tags: [admin]
    security:
      - adminCookie: []
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [kind, count]
            properties:
              kind: {type: string, enum: [invite, trial, recharge]}
              count: {type: integer, minimum: 1, maximum: 1000}
              grantedBalance: {type: integer, description: recharge 码赠送积分}
              grantedDays: {type: integer, description: trial 码赠送天数}
              tier: {type: string}
              expireDays: {type: integer, description: 过期天数}
              note: {type: string}
    responses:
      200:
        description: 签发成功(返回明文 code 列表)
      400:
        description: 参数错误(BAD_REQUEST)
      401:
        description: 未登录(ADMIN_LOGIN_REQUIRED)
    """
    try:
        payload = AdminIssueCodesRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as e:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": e.errors()}) from e

    items = issueCodes(
        kind=payload.kind,
        count=payload.count,
        grantedBalance=payload.grantedBalance,
        grantedDays=payload.grantedDays,
        tier=payload.tier,
        expireDays=payload.expireDays,
        note=payload.note,
        issuedBy=_actor(),
    )
    data = AdminIssueCodesResponse(items=[AdminIssuedCodeItem(**i) for i in items]).model_dump(mode="json")
    return successEnvelope(data)


@bp.get("")
@requireAdminCookie
def listCodesRoute():
    """凭证列表(不含明文 code;支持 kind / status 过滤)。

    ---
    tags: [admin]
    security:
      - adminCookie: []
    parameters:
      - name: kind
        in: query
        required: false
        schema: {type: string}
      - name: status
        in: query
        required: false
        schema: {type: string}
      - name: limit
        in: query
        required: false
        schema: {type: integer, default: 50}
      - name: cursor
        in: query
        required: false
        schema: {type: string}
    responses:
      200:
        description: 凭证列表
      401:
        description: 未登录(ADMIN_LOGIN_REQUIRED)
    """
    kind = (request.args.get("kind") or "").strip() or None
    status = (request.args.get("status") or "").strip() or None
    limit = int(request.args.get("limit", 50))
    cursor = request.args.get("cursor")
    items, nextCursor = listCodes(kind=kind, status=status, limit=limit, cursor=cursor)
    data = AdminCodeListResponse(
        items=[AdminCodeListItem(**i) for i in items],
        nextCursor=nextCursor,
    ).model_dump(mode="json")
    return successEnvelope(data)


@bp.get("/lookup")
@requireAdminCookie
def codeLookupRoute():
    """按明文 code 查状态(hash 后查 license_codes)。

    ---
    tags: [admin]
    security:
      - adminCookie: []
    parameters:
      - name: code
        in: query
        required: true
        schema: {type: string}
    responses:
      200:
        description: 凭证状态
      400:
        description: 缺少 code 参数(BAD_REQUEST)
      401:
        description: 未登录(ADMIN_LOGIN_REQUIRED)
    """
    raw = (request.args.get("code") or "").strip()
    if not raw:
        raise ApiError("BAD_REQUEST", "缺少 code 参数")
    result = lookupCode(raw)
    data = AdminCodeLookupResponse(**result).model_dump(mode="json")
    return successEnvelope(data)


@bp.post("/<string:codeHash>/revoke")
@requireAdminCookie
def revokeCodeRoute(codeHash: str):
    """撤销某凭证(active → revoked)。

    ---
    tags: [admin]
    security:
      - adminCookie: []
    parameters:
      - name: codeHash
        in: path
        required: true
        schema: {type: string}
    responses:
      200:
        description: 撤销成功
      401:
        description: 未登录(ADMIN_LOGIN_REQUIRED)
      404:
        description: 凭证不存在(NOT_FOUND)
    """
    result = revokeCode(codeHash)
    data = AdminCodeRevokeResponse(**result).model_dump()
    return successEnvelope(data)


__all__ = ["bp"]
