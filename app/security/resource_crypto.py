"""资源 KMS 信封加密、设备密钥证明和签名清单实现。"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import Settings, getSettings
from app.errors import ApiError

DEVICE_KEY_PROOF_PREFIX = "prismatica-device-resource-key-v1"
DEVICE_KEY_WRAP_ALGORITHM = "X25519-HKDF-SHA256-AES256-GCM"


def _decodeBase64(value: str, label: str) -> bytes:
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as error:
        raise ApiError("RESOURCE_DEVICE_KEY_INVALID", f"{label}格式无效", httpStatus=400) from error


def _encodeBase64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def canonicalJson(payload: dict[str, Any]) -> bytes:
    """生成跨端稳定的 UTF-8 JSON 签名原文。"""
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def buildDeviceKeyProofMessage(
    deviceId: str,
    encryptionPublicKey: str,
    signingPublicKey: str,
) -> bytes:
    """构造设备公钥自证明的固定原文。"""
    return (
        f"{DEVICE_KEY_PROOF_PREFIX}\n{deviceId}\n"
        f"{encryptionPublicKey}\n{signingPublicKey}"
    ).encode()


def verifyDeviceKeyProof(
    deviceId: str,
    encryptionPublicKey: str,
    signingPublicKey: str,
    proof: str,
) -> None:
    """验证客户端确实持有所提交 Ed25519 公钥对应的私钥。"""
    encryptionKeyBytes = _decodeBase64(encryptionPublicKey, "设备加密公钥")
    signingKeyBytes = _decodeBase64(signingPublicKey, "设备签名公钥")
    proofBytes = _decodeBase64(proof, "设备密钥证明")
    if len(encryptionKeyBytes) != 32 or len(signingKeyBytes) != 32 or len(proofBytes) != 64:
        raise ApiError("RESOURCE_DEVICE_KEY_INVALID", httpStatus=400)
    try:
        x25519.X25519PublicKey.from_public_bytes(encryptionKeyBytes)
        signingKey = ed25519.Ed25519PublicKey.from_public_bytes(signingKeyBytes)
        signingKey.verify(
            proofBytes,
            buildDeviceKeyProofMessage(
                deviceId,
                encryptionPublicKey,
                signingPublicKey,
            ),
        )
    except (ValueError, InvalidSignature) as error:
        raise ApiError("RESOURCE_DEVICE_KEY_INVALID", httpStatus=400) from error


def deviceKeyFingerprint(encryptionPublicKey: str, signingPublicKey: str) -> str:
    """返回可审计但不可反推公钥的设备密钥指纹。"""
    digest = hashlib.sha256(
        f"{encryptionPublicKey}:{signingPublicKey}".encode("ascii")
    ).hexdigest()
    return digest[:24]


def resourceEncryptionContext(resourceKey: str, version: str) -> dict[str, str]:
    """构造 KMS 加解密必须完全一致的非敏感上下文。"""
    return {
        "application": "prismatica",
        "resource": resourceKey,
        "version": version,
    }


def _kmsClient(settings: Settings):
    try:
        import boto3
    except ImportError as error:
        raise ApiError(
            "RESOURCE_KMS_UNAVAILABLE",
            "服务器未安装 AWS KMS 客户端",
            httpStatus=503,
        ) from error
    keywordArguments: dict[str, str] = {}
    if settings.resourceKmsRegion.strip():
        keywordArguments["region_name"] = settings.resourceKmsRegion.strip()
    if settings.resourceKmsEndpointUrl.strip():
        keywordArguments["endpoint_url"] = settings.resourceKmsEndpointUrl.strip()
    return boto3.client("kms", **keywordArguments)


def _validateKmsProvider(settings: Settings) -> str:
    provider = settings.resourceKmsProvider.strip().lower()
    if provider not in {"aws", "local"}:
        raise ApiError("RESOURCE_NOT_CONFIGURED", "资源 KMS 提供方无效", httpStatus=503)
    if provider == "local" and settings.env.strip().lower() in {"prod", "production"}:
        raise ApiError(
            "RESOURCE_NOT_CONFIGURED",
            "生产环境禁止使用本地资源主密钥",
            httpStatus=503,
        )
    return provider


def decryptResourceDataKey(
    wrappedKey: str,
    resourceKey: str,
    version: str,
    settings: Settings | None = None,
) -> bytes:
    """经 KMS 解封资源 DEK；明文密钥仅短暂存在于服务端内存。"""
    currentSettings = settings or getSettings()
    provider = _validateKmsProvider(currentSettings)
    context = resourceEncryptionContext(resourceKey, version)
    try:
        wrappedKeyBytes = _decodeBase64(wrappedKey, "KMS 封装密钥")
        if provider == "aws":
            requestArguments: dict[str, Any] = {
                "CiphertextBlob": wrappedKeyBytes,
                "EncryptionContext": context,
            }
            if currentSettings.resourceKmsKeyId.strip():
                requestArguments["KeyId"] = currentSettings.resourceKmsKeyId.strip()
            response = _kmsClient(currentSettings).decrypt(
                **requestArguments,
            )
            dataKey = bytes(response["Plaintext"])
        else:
            masterKey = _decodeBase64(
                currentSettings.resourceKmsLocalKey.strip(),
                "本地 KMS 主密钥",
            )
            if len(masterKey) != 32 or len(wrappedKeyBytes) < 29:
                raise ValueError("本地 KMS 密钥长度无效")
            dataKey = AESGCM(masterKey).decrypt(
                wrappedKeyBytes[:12],
                wrappedKeyBytes[12:],
                canonicalJson(context),
            )
    except ApiError as error:
        raise ApiError("RESOURCE_KMS_UNAVAILABLE", httpStatus=503) from error
    except Exception as error:
        raise ApiError("RESOURCE_KMS_UNAVAILABLE", httpStatus=503) from error
    if len(dataKey) != 32:
        raise ApiError("RESOURCE_KMS_UNAVAILABLE", "KMS 返回的数据密钥长度无效", httpStatus=503)
    return dataKey


def generateResourceDataKey(
    resourceKey: str,
    version: str,
    settings: Settings | None = None,
) -> tuple[bytes, str]:
    """供发布脚本生成 SQLCipher DEK 及其 KMS 密文。"""
    currentSettings = settings or getSettings()
    provider = _validateKmsProvider(currentSettings)
    context = resourceEncryptionContext(resourceKey, version)
    if provider == "aws":
        if not currentSettings.resourceKmsKeyId.strip():
            raise ApiError("RESOURCE_NOT_CONFIGURED", "RESOURCE_KMS_KEY_ID 未配置", httpStatus=503)
        response = _kmsClient(currentSettings).generate_data_key(
            KeyId=currentSettings.resourceKmsKeyId.strip(),
            KeySpec="AES_256",
            EncryptionContext=context,
        )
        return bytes(response["Plaintext"]), _encodeBase64(bytes(response["CiphertextBlob"]))

    try:
        masterKey = _decodeBase64(
            currentSettings.resourceKmsLocalKey.strip(),
            "本地 KMS 主密钥",
        )
    except ApiError as error:
        raise ApiError("RESOURCE_NOT_CONFIGURED", "本地 KMS 主密钥无效", httpStatus=503) from error
    if len(masterKey) != 32:
        raise ApiError("RESOURCE_NOT_CONFIGURED", "本地 KMS 主密钥必须为 32 字节", httpStatus=503)
    dataKey = os.urandom(32)
    nonce = os.urandom(12)
    wrappedKey = nonce + AESGCM(masterKey).encrypt(
        nonce,
        dataKey,
        canonicalJson(context),
    )
    return dataKey, _encodeBase64(wrappedKey)


@dataclass(frozen=True)
class DeviceWrappedKey:
    """设备专属 DEK 封装结果。"""

    algorithm: str
    ephemeralPublicKey: str
    nonce: str
    ciphertext: str


def wrapDataKeyForDevice(
    dataKey: bytes,
    devicePublicKey: str,
    userId: int,
    deviceId: str,
    resourceKey: str,
    version: str,
) -> DeviceWrappedKey:
    """使用临时 X25519 密钥把资源 DEK 仅封装给目标设备。"""
    publicKeyBytes = _decodeBase64(devicePublicKey, "设备加密公钥")
    if len(dataKey) != 32 or len(publicKeyBytes) != 32:
        raise ApiError("RESOURCE_DEVICE_KEY_INVALID", httpStatus=400)
    try:
        deviceKey = x25519.X25519PublicKey.from_public_bytes(publicKeyBytes)
        ephemeralPrivateKey = x25519.X25519PrivateKey.generate()
        ephemeralPublicKey = ephemeralPrivateKey.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        context = (
            f"prismatica-resource-wrap-v1\n{userId}\n{deviceId}\n"
            f"{resourceKey}\n{version}"
        ).encode()
        wrappingKey = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=context,
        ).derive(ephemeralPrivateKey.exchange(deviceKey))
        nonce = os.urandom(12)
        ciphertext = AESGCM(wrappingKey).encrypt(nonce, dataKey, context)
    except ValueError as error:
        raise ApiError("RESOURCE_DEVICE_KEY_INVALID", httpStatus=400) from error
    return DeviceWrappedKey(
        algorithm=DEVICE_KEY_WRAP_ALGORITHM,
        ephemeralPublicKey=_encodeBase64(ephemeralPublicKey),
        nonce=_encodeBase64(nonce),
        ciphertext=_encodeBase64(ciphertext),
    )


def signResourceManifest(
    payload: dict[str, Any],
    settings: Settings | None = None,
) -> tuple[str, str, str]:
    """使用本地 Ed25519 或 AWS KMS P-256 对规范化清单签名。"""
    currentSettings = settings or getSettings()
    provider = currentSettings.resourceManifestSignerProvider.strip().lower()
    keyId = currentSettings.resourceManifestSigningKeyId.strip()
    message = canonicalJson(payload)
    try:
        if provider == "aws":
            if not keyId:
                raise ValueError("KMS 签名密钥 ID 未配置")
            response = _kmsClient(currentSettings).sign(
                KeyId=keyId,
                Message=hashlib.sha256(message).digest(),
                MessageType="DIGEST",
                SigningAlgorithm="ECDSA_SHA_256",
            )
            return "ECDSA_P256_SHA256", keyId, _encodeBase64(bytes(response["Signature"]))
        if provider != "local":
            raise ValueError("签名提供方无效")
        if currentSettings.env.strip().lower() in {"prod", "production"}:
            raise ValueError("生产环境禁止使用本地清单签名私钥")
        privateKeyBytes = _decodeBase64(
            currentSettings.resourceManifestSigningPrivateKey.strip(),
            "清单签名私钥",
        )
        privateKey = serialization.load_der_private_key(privateKeyBytes, password=None)
        if not isinstance(privateKey, ed25519.Ed25519PrivateKey):
            raise ValueError("清单签名私钥不是 Ed25519")
        return "Ed25519", keyId or "local-dev", _encodeBase64(privateKey.sign(message))
    except ApiError as error:
        raise ApiError("RESOURCE_SIGNING_UNAVAILABLE", httpStatus=503) from error
    except Exception as error:
        raise ApiError("RESOURCE_SIGNING_UNAVAILABLE", httpStatus=503) from error


__all__ = [
    "DEVICE_KEY_WRAP_ALGORITHM",
    "DeviceWrappedKey",
    "buildDeviceKeyProofMessage",
    "canonicalJson",
    "decryptResourceDataKey",
    "deviceKeyFingerprint",
    "generateResourceDataKey",
    "resourceEncryptionContext",
    "signResourceManifest",
    "verifyDeviceKeyProof",
    "wrapDataKeyForDevice",
]
