"""受保护资源短期下载票据签发与校验。"""

from __future__ import annotations

import base64
import binascii
import hmac
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import jwt

from app.config import getSettings
from app.errors import ApiError

RESOURCE_TICKET_AUDIENCE = "prismatica-resource-download"
RESOURCE_TICKET_TYPE = "resource_download"


@dataclass(frozen=True)
class ResourceTicketClaims:
    """校验成功后的资源票据上下文。"""

    userId: int
    deviceId: str
    resourceKey: str
    resourceVersion: str
    jti: str


def _ticketSecret() -> str:
    settings = getSettings()
    secret = settings.resourceTicketSecret.strip()
    if len(secret.encode("utf-8")) < 32 or secret.startswith("DEV-"):
        raise ApiError(
            "RESOURCE_NOT_CONFIGURED",
            "资源票据签名密钥尚未安全配置",
            httpStatus=503,
        )
    return secret


def _isCanonicalJwt(ticket: str) -> bool:
    """拒绝 Base64URL 尾部位不同但解码字节相同的非规范 JWT。"""
    parts = ticket.split(".")
    if len(parts) != 3 or any(not part for part in parts):
        return False
    try:
        for part in parts:
            padded = part + "=" * (-len(part) % 4)
            decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
            canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
            if not hmac.compare_digest(part, canonical):
                return False
    except (UnicodeEncodeError, binascii.Error, ValueError):
        return False
    return True


def createResourceTicket(
    userId: int,
    deviceId: str,
    resourceKey: str,
    resourceVersion: str,
    ttlSec: int | None = None,
) -> str:
    """签发仅能下载一个资源版本的短期票据。"""
    settings = getSettings()
    expiresIn = int(ttlSec or settings.resourceTicketTtlSec)
    if expiresIn <= 0:
        raise ValueError("资源票据 TTL 必须大于 0")
    if not deviceId or not resourceKey or not resourceVersion:
        raise ValueError("资源票据参数不完整")

    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": settings.jwtIssuer,
        "aud": RESOURCE_TICKET_AUDIENCE,
        "sub": str(userId),
        "device_id": deviceId,
        "resource_key": resourceKey,
        "resource_version": resourceVersion,
        "token_type": RESOURCE_TICKET_TYPE,
        "jti": str(uuid4()),
        "iat": now,
        "exp": now + expiresIn,
    }
    return jwt.encode(
        payload,
        _ticketSecret(),
        algorithm="HS256",
    )


def verifyResourceTicket(
    ticket: str,
    expectedResourceKey: str,
    expectedVersion: str,
) -> ResourceTicketClaims:
    """校验票据签名、时效、资源标识和版本。"""
    settings = getSettings()
    if not _isCanonicalJwt(ticket):
        raise ApiError("RESOURCE_TICKET_INVALID", httpStatus=401)
    try:
        payload = jwt.decode(
            ticket,
            _ticketSecret(),
            algorithms=["HS256"],
            audience=RESOURCE_TICKET_AUDIENCE,
            issuer=settings.jwtIssuer,
            options={
                "require": [
                    "sub",
                    "device_id",
                    "resource_key",
                    "resource_version",
                    "jti",
                    "iat",
                    "exp",
                ]
            },
        )
    except jwt.ExpiredSignatureError as error:
        raise ApiError("RESOURCE_TICKET_EXPIRED", httpStatus=401) from error
    except jwt.InvalidTokenError as error:
        raise ApiError("RESOURCE_TICKET_INVALID", httpStatus=401) from error

    if payload.get("token_type") != RESOURCE_TICKET_TYPE:
        raise ApiError("RESOURCE_TICKET_INVALID", httpStatus=401)
    if payload.get("resource_key") != expectedResourceKey:
        raise ApiError("RESOURCE_TICKET_INVALID", httpStatus=401)
    if str(payload.get("resource_version")) != str(expectedVersion):
        raise ApiError("RESOURCE_TICKET_INVALID", httpStatus=401)

    subject = str(payload.get("sub", ""))
    deviceId = str(payload.get("device_id", ""))
    jti = str(payload.get("jti", ""))
    if not subject.isdecimal() or not deviceId or not jti:
        raise ApiError("RESOURCE_TICKET_INVALID", httpStatus=401)
    return ResourceTicketClaims(
        userId=int(subject),
        deviceId=deviceId,
        resourceKey=expectedResourceKey,
        resourceVersion=str(expectedVersion),
        jti=jti,
    )


__all__ = [
    "ResourceTicketClaims",
    "createResourceTicket",
    "verifyResourceTicket",
]
