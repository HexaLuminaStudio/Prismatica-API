"""HSK 作文数据库资源目录、订阅授权与短期清单服务。"""

from __future__ import annotations

import re
from base64 import b64decode
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, getSettings
from app.errors import ApiError
from app.models.identity import IdentityDevice
from app.models.identity import User as IdentityUser
from app.models.subscription import Subscription
from app.schemas.resources import (
    DeviceResourceKeyRequest,
    DeviceResourceKeyResponse,
    ResourceBootstrapResponse,
    ResourceManifestOut,
    ResourceWrappedKeyOut,
)
from app.security.resource_crypto import (
    decryptResourceDataKey,
    deviceKeyFingerprint,
    signResourceManifest,
    verifyDeviceKeyProof,
    wrapDataKeyForDevice,
)
from app.security.resource_ticket import createResourceTicket


@dataclass(frozen=True)
class ProtectedResource:
    """一个只在后端持有真实源地址的受保护资源。"""

    key: str
    displayName: str
    fileName: str
    sourceUrl: str
    sha256: str
    version: str
    wrappedKey: str


def getResourceCatalog(settings: Settings | None = None) -> dict[str, ProtectedResource]:
    """从后端环境配置构建资源目录，不向客户端暴露源站地址。"""
    currentSettings = settings or getSettings()
    resources = (
        ProtectedResource(
            key="hskCorpus",
            displayName="HSK 作文数据表",
            fileName="hsk_corpus.db",
            sourceUrl=currentSettings.hskCorpusSourceUrl.strip(),
            sha256=currentSettings.hskCorpusSha256.strip().lower(),
            version=currentSettings.hskCorpusVersion.strip(),
            wrappedKey=currentSettings.hskCorpusWrappedKey.strip(),
        ),
        ProtectedResource(
            key="hskLocalCorpus",
            displayName="HSK 作文正文库",
            fileName="hsk_corpus_local.db",
            sourceUrl=currentSettings.hskLocalCorpusSourceUrl.strip(),
            sha256=currentSettings.hskLocalCorpusSha256.strip().lower(),
            version=currentSettings.hskLocalCorpusVersion.strip(),
            wrappedKey=currentSettings.hskLocalCorpusWrappedKey.strip(),
        ),
    )
    return {resource.key: resource for resource in resources}


def validateResourceConfiguration(resource: ProtectedResource) -> None:
    """拒绝空地址、非 HTTP(S) 地址和无完整哈希的资源配置。"""
    parsedUrl = urlparse(resource.sourceUrl)
    if parsedUrl.scheme not in {"http", "https"} or not parsedUrl.netloc:
        raise ApiError(
            "RESOURCE_NOT_CONFIGURED",
            f"{resource.displayName}的后端源地址尚未配置",
            httpStatus=503,
        )
    if not re.fullmatch(r"[a-f0-9]{64}", resource.sha256):
        raise ApiError(
            "RESOURCE_NOT_CONFIGURED",
            f"{resource.displayName}的 SHA-256 尚未配置",
            httpStatus=503,
        )
    if not resource.version:
        raise ApiError(
            "RESOURCE_NOT_CONFIGURED",
            f"{resource.displayName}的版本尚未配置",
            httpStatus=503,
        )
    try:
        if len(b64decode(resource.wrappedKey, validate=True)) < 29:
            raise ValueError("wrapped key is too short")
    except (ValueError, TypeError) as error:
        raise ApiError(
            "RESOURCE_NOT_CONFIGURED",
            f"{resource.displayName}的 KMS 封装密钥尚未配置",
            httpStatus=503,
        ) from error


def registerDeviceResourceKey(
    db: Session,
    userId: int,
    deviceId: str,
    payload: DeviceResourceKeyRequest,
) -> DeviceResourceKeyResponse:
    """首次绑定设备资源公钥；已有密钥不可被静默替换。"""
    device = db.execute(
        select(IdentityDevice)
        .where(
            IdentityDevice.userId == userId,
            IdentityDevice.deviceId == deviceId,
            IdentityDevice.status == "active",
        )
        .with_for_update()
    ).scalar_one_or_none()
    if device is None:
        raise ApiError("FORBIDDEN", "当前设备未授权或已被撤销", httpStatus=403)

    verifyDeviceKeyProof(
        deviceId,
        payload.encryptionPublicKey,
        payload.signingPublicKey,
        payload.proof,
    )
    registered = device.resourceEncryptionPublicKey is None
    if registered:
        device.resourceEncryptionPublicKey = payload.encryptionPublicKey
        device.resourceSigningPublicKey = payload.signingPublicKey
        device.resourceKeyUpdatedAt = datetime.now(UTC).replace(tzinfo=None)
        db.commit()
    elif (
        device.resourceEncryptionPublicKey != payload.encryptionPublicKey
        or device.resourceSigningPublicKey != payload.signingPublicKey
    ):
        raise ApiError(
            "RESOURCE_DEVICE_KEY_CONFLICT",
            "该设备已绑定其他资源密钥，请先撤销设备后重新登录",
            httpStatus=409,
        )

    return DeviceResourceKeyResponse(
        deviceId=deviceId,
        keyFingerprint=deviceKeyFingerprint(
            payload.encryptionPublicKey,
            payload.signingPublicKey,
        ),
        registered=registered,
    )


def authorizeResourceAccess(db: Session, userId: int, deviceId: str) -> Subscription:
    """校验账号、当前设备和有效订阅，返回生效订阅。"""
    user = db.get(IdentityUser, userId)
    if user is None or user.deletedAt is not None or user.status != "active":
        raise ApiError("FORBIDDEN", "账号状态异常，无法下载资源", httpStatus=403)

    activeDevice = db.execute(
        select(IdentityDevice).where(
            IdentityDevice.userId == userId,
            IdentityDevice.deviceId == deviceId,
            IdentityDevice.status == "active",
        )
    ).scalar_one_or_none()
    if activeDevice is None:
        raise ApiError("FORBIDDEN", "当前设备未授权或已被撤销", httpStatus=403)

    now = datetime.now(UTC).replace(tzinfo=None)
    subscription = db.execute(
        select(Subscription)
        .where(
            Subscription.userId == userId,
            Subscription.status == "active",
            Subscription.expiresAt > now,
        )
        .order_by(Subscription.currentPeriodEnd.desc())
        .limit(1)
    ).scalar_one_or_none()
    if subscription is None:
        raise ApiError("RESOURCE_SUBSCRIPTION_REQUIRED", httpStatus=403)
    return subscription


def buildResourceManifests(
    db: Session,
    userId: int,
    deviceId: str,
    publicBaseUrl: str,
) -> list[ResourceManifestOut]:
    """授权通过后，为全部数据库签发短期下载地址。"""
    authorizeResourceAccess(db, userId, deviceId)
    device = db.execute(
        select(IdentityDevice).where(
            IdentityDevice.userId == userId,
            IdentityDevice.deviceId == deviceId,
            IdentityDevice.status == "active",
        )
    ).scalar_one_or_none()
    if device is None or not device.resourceEncryptionPublicKey:
        raise ApiError("RESOURCE_DEVICE_KEY_REQUIRED", httpStatus=428)
    settings = getSettings()
    baseUrl = publicBaseUrl.strip().rstrip("/")
    if not baseUrl:
        raise ApiError("RESOURCE_NOT_CONFIGURED", "资源 API 公网地址不可用", httpStatus=503)

    manifests = []
    for resource in getResourceCatalog(settings).values():
        validateResourceConfiguration(resource)
        ticket = createResourceTicket(
            userId,
            deviceId,
            resource.key,
            resource.version,
        )
        downloadUrl = (
            f"{baseUrl}/v1/resources/download/{resource.key}"
            f"?ticket={quote(ticket, safe='')}"
        )
        dataKey = decryptResourceDataKey(
            resource.wrappedKey,
            resource.key,
            resource.version,
            settings,
        )
        wrappedKey = wrapDataKeyForDevice(
            dataKey,
            device.resourceEncryptionPublicKey,
            userId,
            deviceId,
            resource.key,
            resource.version,
        )
        manifests.append(
            ResourceManifestOut(
                resourceKey=resource.key,
                displayName=resource.displayName,
                fileName=resource.fileName,
                version=resource.version,
                sha256=resource.sha256,
                downloadUrl=downloadUrl,
                sqlCipherCompatibility=4,
                wrappedDatabaseKey=ResourceWrappedKeyOut(
                    algorithm=wrappedKey.algorithm,
                    ephemeralPublicKey=wrappedKey.ephemeralPublicKey,
                    nonce=wrappedKey.nonce,
                    ciphertext=wrappedKey.ciphertext,
                ),
            )
        )
    return manifests


def buildSignedResourceBootstrap(
    db: Session,
    userId: int,
    deviceId: str,
    publicBaseUrl: str,
) -> ResourceBootstrapResponse:
    """构建绑定设备、短期有效并经过签名的资源清单。"""
    settings = getSettings()
    issuedAt = datetime.now(UTC)
    expiresAt = issuedAt + timedelta(seconds=settings.resourceTicketTtlSec)
    manifests = buildResourceManifests(db, userId, deviceId, publicBaseUrl)
    unsignedPayload = {
        "manifestVersion": 1,
        "issuedAt": issuedAt.isoformat().replace("+00:00", "Z"),
        "expiresAt": expiresAt.isoformat().replace("+00:00", "Z"),
        "deviceId": deviceId,
        "resources": [manifest.model_dump(mode="json") for manifest in manifests],
    }
    signatureAlgorithm, signingKeyId, signature = signResourceManifest(
        unsignedPayload,
        settings,
    )
    return ResourceBootstrapResponse(
        **unsignedPayload,
        signatureAlgorithm=signatureAlgorithm,
        signingKeyId=signingKeyId,
        signature=signature,
    )


def getConfiguredResource(resourceKey: str) -> ProtectedResource:
    """读取一个已配置资源；票据中不存在的 key 一律按 404 处理。"""
    resource = getResourceCatalog().get(resourceKey)
    if resource is None:
        raise ApiError("NOT_FOUND", "资源不存在", httpStatus=404)
    validateResourceConfiguration(resource)
    return resource


__all__ = [
    "ProtectedResource",
    "authorizeResourceAccess",
    "buildSignedResourceBootstrap",
    "buildResourceManifests",
    "getConfiguredResource",
    "getResourceCatalog",
    "registerDeviceResourceKey",
    "validateResourceConfiguration",
]
