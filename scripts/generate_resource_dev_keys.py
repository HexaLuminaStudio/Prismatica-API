"""生成仅供本地开发使用的资源 KMS 与清单签名密钥。"""

from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


DEV_KEY_NAMES = (
    "RESOURCE_KMS_PROVIDER",
    "RESOURCE_KMS_LOCAL_KEY",
    "RESOURCE_MANIFEST_SIGNER_PROVIDER",
    "RESOURCE_MANIFEST_SIGNING_KEY_ID",
    "RESOURCE_MANIFEST_SIGNING_PRIVATE_KEY",
)


def _writeEnvFile(envPath: Path, values: dict[str, str], force: bool) -> None:
    """把开发密钥写入本地 env 文件，避免在终端输出私钥。"""
    existingLines = (
        envPath.read_text(encoding="utf-8").splitlines() if envPath.exists() else []
    )
    existingValues: dict[str, str] = {}
    for line in existingLines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", maxsplit=1)
        existingValues[name.strip()] = value.strip().strip('"').strip("'")

    if existingValues.get("ENV", "dev").lower() in {"prod", "production"}:
        raise RuntimeError("禁止向生产环境文件写入本地开发密钥")
    configuredNames = [
        name for name in DEV_KEY_NAMES if existingValues.get(name, "").strip()
    ]
    if configuredNames and not force:
        names = ", ".join(configuredNames)
        raise RuntimeError(f"开发密钥已经存在：{names}；确认轮换后使用 --force")

    remainingValues = dict(values)
    updatedLines: list[str] = []
    for line in existingLines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            name = stripped.split("=", maxsplit=1)[0].strip()
            if name in remainingValues:
                updatedLines.append(f"{name}={remainingValues.pop(name)}")
                continue
        updatedLines.append(line)
    if remainingValues:
        if updatedLines and updatedLines[-1].strip():
            updatedLines.append("")
        updatedLines.append("# 本机资源加密开发密钥；禁止提交或用于生产")
        updatedLines.extend(f"{name}={value}" for name, value in remainingValues.items())

    temporaryPath = envPath.with_suffix(envPath.suffix + ".tmp")
    temporaryPath.write_text("\n".join(updatedLines) + "\n", encoding="utf-8")
    os.replace(temporaryPath, envPath)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成本地资源加密开发密钥")
    parser.add_argument(
        "--write-env",
        dest="writeEnv",
        type=Path,
        help="把私钥安全写入指定 .env 文件，不在终端显示",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="轮换指定 .env 中已经存在的本地开发密钥",
    )
    arguments = parser.parse_args()

    privateKey = ed25519.Ed25519PrivateKey.generate()
    privateKeyDer = privateKey.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    publicKeyDer = privateKey.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    values = {
        "RESOURCE_KMS_PROVIDER": "local",
        "RESOURCE_KMS_LOCAL_KEY": base64.b64encode(os.urandom(32)).decode("ascii"),
        "RESOURCE_MANIFEST_SIGNER_PROVIDER": "local",
        "RESOURCE_MANIFEST_SIGNING_KEY_ID": "local-dev",
        "RESOURCE_MANIFEST_SIGNING_PRIVATE_KEY": base64.b64encode(
            privateKeyDer
        ).decode("ascii"),
    }
    if arguments.writeEnv:
        _writeEnvFile(arguments.writeEnv.resolve(), values, arguments.force)
        print(f"[OK] 本地开发密钥已写入：{arguments.writeEnv.resolve()}")
    else:
        for name, value in values.items():
            print(f"{name}={value}")
    print("PRISMATICA_RESOURCE_MANIFEST_KEY_ID=local-dev")
    print(
        "PRISMATICA_RESOURCE_MANIFEST_PUBLIC_KEY_B64="
        f"{base64.b64encode(publicKeyDer).decode('ascii')}"
    )
    print("以上私钥仅供本机开发，不得用于生产或提交到 Git。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
