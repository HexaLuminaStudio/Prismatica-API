"""受保护资源设备密钥、签名清单与下载响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DeviceResourceKeyRequest(BaseModel):
    """当前设备首次注册的资源加密与签名公钥。"""

    encryptionPublicKey: str = Field(min_length=43, max_length=44)
    signingPublicKey: str = Field(min_length=43, max_length=44)
    proof: str = Field(min_length=86, max_length=88)


class DeviceResourceKeyResponse(BaseModel):
    """设备资源密钥注册结果。"""

    deviceId: str
    keyFingerprint: str
    registered: bool


class ResourceWrappedKeyOut(BaseModel):
    """仅可由目标设备私钥解封的 SQLCipher 数据库密钥。"""

    algorithm: str
    ephemeralPublicKey: str
    nonce: str
    ciphertext: str


class ResourceManifestOut(BaseModel):
    """单个加密资源的短期下载清单。"""

    resourceKey: str
    displayName: str
    fileName: str
    version: str
    sha256: str
    downloadUrl: str
    sqlCipherCompatibility: int = 4
    wrappedDatabaseKey: ResourceWrappedKeyOut


class ResourceBootstrapResponse(BaseModel):
    """绑定设备、带签名的完整资源清单。"""

    manifestVersion: int = 1
    issuedAt: str
    expiresAt: str
    deviceId: str
    resources: list[ResourceManifestOut] = Field(default_factory=list)
    signatureAlgorithm: str
    signingKeyId: str
    signature: str


__all__ = [
    "DeviceResourceKeyRequest",
    "DeviceResourceKeyResponse",
    "ResourceBootstrapResponse",
    "ResourceManifestOut",
    "ResourceWrappedKeyOut",
]
