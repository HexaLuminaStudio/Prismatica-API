"""导出可安全内置到桌面客户端的资源清单签名公钥。"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402


def main() -> int:
    settings = Settings()
    provider = settings.resourceManifestSignerProvider.strip().lower()
    keyId = settings.resourceManifestSigningKeyId.strip()
    if provider == "aws":
        import boto3

        keywordArguments = {}
        if settings.resourceKmsRegion.strip():
            keywordArguments["region_name"] = settings.resourceKmsRegion.strip()
        if settings.resourceKmsEndpointUrl.strip():
            keywordArguments["endpoint_url"] = settings.resourceKmsEndpointUrl.strip()
        if not keyId:
            raise RuntimeError("RESOURCE_MANIFEST_SIGNING_KEY_ID 未配置")
        response = boto3.client("kms", **keywordArguments).get_public_key(KeyId=keyId)
        publicKeyDer = bytes(response["PublicKey"])
    elif provider == "local":
        privateKeyDer = base64.b64decode(
            settings.resourceManifestSigningPrivateKey.encode("ascii"),
            validate=True,
        )
        privateKey = serialization.load_der_private_key(privateKeyDer, password=None)
        if not isinstance(privateKey, ed25519.Ed25519PrivateKey):
            raise RuntimeError("本地清单签名私钥不是 Ed25519")
        publicKeyDer = privateKey.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        keyId = keyId or "local-dev"
    else:
        raise RuntimeError("RESOURCE_MANIFEST_SIGNER_PROVIDER 无效")

    print(f"PRISMATICA_RESOURCE_MANIFEST_KEY_ID={keyId}")
    print(
        "PRISMATICA_RESOURCE_MANIFEST_PUBLIC_KEY_B64="
        f"{base64.b64encode(publicKeyDer).decode('ascii')}"
    )
    print("公钥可公开；请勿导出或复制 KMS 私钥。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
