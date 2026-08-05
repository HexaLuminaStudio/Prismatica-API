"""HMAC-SHA256 凭证验签。

复用客户端 signed_code.py 的格式约定:
    base64( JSON( payload + {"signature": "<hmac-hex>"} ) )
    payload 用 sort_keys + ensure_ascii=False + 紧凑分隔符规范化
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime
from typing import Any

from app.config import getSettings

_settings = getSettings()


def _canonicalPayload(payload: dict[str, Any]) -> str:
    """构造签名前的规范化 JSON 字符串。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def signPayload(payload: dict[str, Any]) -> str:
    """HMAC-SHA256 签名(16 进制)。"""
    canonical = _canonicalPayload(payload)
    return hmac.new(
        _settings.licenseSecret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verifyPayload(payload: dict[str, Any], signature: str) -> bool:
    """验签(防时序攻击)。"""
    if not signature or not isinstance(signature, str):
        return False
    expected = signPayload(payload)
    return hmac.compare_digest(expected, signature)


def decodeSignedCode(rawCode: str) -> dict[str, Any]:
    """base64 → JSON → dict(未验签)。失败抛 ValueError。"""
    try:
        decoded = base64.b64decode(rawCode).decode("utf-8")
        data = json.loads(decoded)
    except Exception as e:
        raise ValueError(f"凭证格式错误: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("凭证结构非法")
    return data


def parseSignedCode(
    rawCode: str,
    modelCls: type,
) -> tuple[Any, str]:
    """解码 + 验签 + 反序列化为 Pydantic 模型。

    Returns:
        (model, signature)
    """
    data = decodeSignedCode(rawCode)
    signature = data.get("signature")
    if not signature:
        raise ValueError("凭证缺少 signature 字段")
    payloadWithoutSig = {k: v for k, v in data.items() if k != "signature"}
    if not verifyPayload(payloadWithoutSig, signature):
        raise ValueError("凭证签名校验失败")
    model = modelCls.model_validate(payloadWithoutSig)
    return model, signature


def hashCode(rawCode: str) -> str:
    """对码做 sha256(用于 license_codes_seen.code_hash 幂等)。"""
    return hashlib.sha256(rawCode.encode("utf-8")).hexdigest()


def ensureAware(dt: datetime) -> datetime:
    """datetime 若为 naive,补 UTC tzinfo(避免 MySQL 写入告警)。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=__import__("datetime").timezone.utc)
    return dt
