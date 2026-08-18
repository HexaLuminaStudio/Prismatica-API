"""受保护 HSK 作文数据库清单与短期下载网关。"""

from __future__ import annotations

from collections.abc import Iterator

import requests
from flask import Blueprint, Response, g, request, stream_with_context
from pydantic import ValidationError
from requests.exceptions import RequestException

from app.config import getSettings
from app.db import getDb
from app.deps import requireUser
from app.errors import ApiError, successEnvelope
from app.main import limiter
from app.schemas.official_corpus import (
    OfficialCorpusTokenRequest,
    OfficialCorpusTokenResponse,
)
from app.schemas.resources import ResourceBootstrapResponse
from app.security.resource_ticket import verifyResourceTicket
from app.services.official_corpus_token_service import requestOfficialCorpusToken
from app.services.resource_service import (
    authorizeResourceAccess,
    buildResourceManifests,
    getConfiguredResource,
)

bp = Blueprint("resources", __name__, url_prefix="/v1/resources")


def _publicBaseUrl() -> str:
    settings = getSettings()
    return settings.resourcePublicBaseUrl.strip() or request.url_root.rstrip("/")


@bp.post("/bootstrap")
@limiter.limit("30 per hour")
@requireUser
def bootstrapResources():
    """校验登录账号和设备后签发当前资源清单。"""
    with getDb() as db:
        manifests = buildResourceManifests(
            db,
            int(g.userId),
            str(g.deviceId),
            _publicBaseUrl(),
        )
    response = ResourceBootstrapResponse(resources=manifests)
    return successEnvelope(response.model_dump(mode="json"))


@bp.post("/official-token")
@limiter.limit("10 per hour")
def issueOfficialCorpusToken():
    """由后端官方账号代登录语料平台，客户端永远接触不到账号密码。"""
    deviceId = request.headers.get("X-Device-Id", "").strip()
    if not 8 <= len(deviceId) <= 128:
        raise ApiError("BAD_REQUEST", "缺少有效的 X-Device-Id")
    try:
        payload = OfficialCorpusTokenRequest.model_validate(request.get_json(silent=True) or {})
    except ValidationError as error:
        raise ApiError(
            "BAD_REQUEST",
            "provider 必须为 hsk 或 global",
            details={"errors": error.errors()},
        ) from error

    token = requestOfficialCorpusToken(payload.provider)
    response = OfficialCorpusTokenResponse(provider=payload.provider, token=token)
    return successEnvelope(response.model_dump(mode="json"))


@bp.get("/download/<string:resourceKey>")
@limiter.limit("20 per hour")
def downloadResource(resourceKey: str):
    """验证短期票据并从后端源站流式转发数据库文件。"""
    resource = getConfiguredResource(resourceKey)
    ticket = request.args.get("ticket", "").strip()
    if not ticket:
        raise ApiError("RESOURCE_TICKET_INVALID", httpStatus=401)
    claims = verifyResourceTicket(ticket, resource.key, resource.version)
    with getDb() as db:
        authorizeResourceAccess(db, claims.userId, claims.deviceId)

    settings = getSettings()
    upstreamResponse = None
    try:
        upstreamResponse = requests.get(
            resource.sourceUrl,
            stream=True,
            allow_redirects=True,
            timeout=(
                settings.resourceUpstreamConnectTimeoutSec,
                settings.resourceUpstreamReadTimeoutSec,
            ),
            headers={"User-Agent": "PrismaticaAPI/1.0 ResourceGateway"},
        )
        upstreamResponse.raise_for_status()
    except RequestException as error:
        if upstreamResponse is not None:
            upstreamResponse.close()
        raise ApiError("RESOURCE_UPSTREAM_UNAVAILABLE", httpStatus=502) from error

    def _streamChunks() -> Iterator[bytes]:
        try:
            for chunk in upstreamResponse.iter_content(chunk_size=256 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstreamResponse.close()

    response = Response(
        stream_with_context(_streamChunks()),
        status=200,
        mimetype="application/octet-stream",
        direct_passthrough=True,
    )
    contentLength = upstreamResponse.headers.get("Content-Length")
    if contentLength and contentLength.isdecimal():
        response.headers["Content-Length"] = contentLength
    response.headers["Content-Disposition"] = f'attachment; filename="{resource.fileName}"'
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


__all__ = ["bp"]
