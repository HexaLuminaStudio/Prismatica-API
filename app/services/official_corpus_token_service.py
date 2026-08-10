"""使用仅存于后端环境变量的官方语料账号换取 Token。"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

import requests
from pydantic import SecretStr
from requests.exceptions import RequestException

from app.config import getSettings
from app.errors import ApiError

OfficialCorpusProvider = Literal["hsk", "global"]


def _secretValue(value: SecretStr | str) -> str:
    if isinstance(value, SecretStr):
        return value.get_secret_value().strip()
    return str(value or "").strip()


def _configuredCredentials(provider: OfficialCorpusProvider) -> tuple[str, str]:
    settings = getSettings()
    if provider == "hsk":
        username = settings.officialHskUsername.strip()
        password = _secretValue(settings.officialHskPassword)
    else:
        username = settings.officialGlobalUsername.strip()
        password = _secretValue(settings.officialGlobalPassword)

    if not username or not password:
        raise ApiError("OFFICIAL_ACCOUNT_UNAVAILABLE", httpStatus=503)
    return username, password


def _requestJson(
    url: str,
    payload: dict[str, str],
) -> tuple[dict, int]:
    settings = getSettings()
    try:
        response = requests.post(
            url,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "PrismaticaAPI/1.0 OfficialTokenGateway",
            },
            json=payload,
            timeout=(
                settings.officialTokenConnectTimeoutSec,
                settings.officialTokenReadTimeoutSec,
            ),
        )
        response.raise_for_status()
        responseText = response.text.strip().lstrip("\ufeff")
        if not responseText:
            raise ValueError("empty response")
        data = json.loads(responseText)
        if not isinstance(data, dict):
            raise ValueError("invalid response shape")
        return data, response.status_code
    except (RequestException, ValueError, json.JSONDecodeError) as error:
        raise ApiError(
            "OFFICIAL_TOKEN_UPSTREAM_UNAVAILABLE",
            httpStatus=502,
        ) from error


def requestOfficialCorpusToken(provider: OfficialCorpusProvider) -> str:
    """向对应语料平台登录，只返回 Token，不泄露后端账号密码。"""
    username, password = _configuredCredentials(provider)

    if provider == "hsk":
        data, _statusCode = _requestJson(
            "https://hsk.blcu.edu.cn/api/v1/login/access-token",
            {"username": username, "password": password},
        )
        token = data.get("data") if data.get("code") == 0 else None
    else:
        passwordDigest = hashlib.md5(password.encode("utf-8")).hexdigest()
        data, _statusCode = _requestJson(
            "https://qqk.blcu.edu.cn/sys/index/login",
            {"UserID": username, "Password": passwordDigest},
        )
        token = data.get("token") if data.get("stats") == "1" else None

    if not token or not str(token).strip():
        raise ApiError("OFFICIAL_ACCOUNT_UNAVAILABLE", httpStatus=503)
    return str(token).strip()


__all__ = ["OfficialCorpusProvider", "requestOfficialCorpusToken"]
