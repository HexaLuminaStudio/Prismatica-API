"""把明文 SQLite 发布为 SQLCipher 4 资源并用 KMS 封装数据密钥。"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlcipher3 import dbapi2 as sqlcipher  # noqa: E402

from app.security.resource_crypto import generateResourceDataKey  # noqa: E402


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as sourceFile:
        for chunk in iter(lambda: sourceFile.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def encryptDatabase(sourcePath: Path, outputPath: Path, dataKey: bytes) -> None:
    """使用 SQLCipher raw 256-bit key 导出一个加密数据库。"""
    connection = sqlcipher.connect(str(outputPath))
    try:
        connection.execute(f"PRAGMA key = \"x'{dataKey.hex()}'\"")
        connection.execute("PRAGMA cipher_compatibility = 4")
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("ATTACH DATABASE ? AS plaintext KEY ''", (str(sourcePath),))
        connection.execute("SELECT sqlcipher_export('main', 'plaintext')")
        connection.execute("DETACH DATABASE plaintext")
        connection.commit()
        if connection.execute("PRAGMA cipher_integrity_check").fetchall():
            raise RuntimeError("SQLCipher 页级完整性检查失败")
        quickCheck = connection.execute("PRAGMA quick_check").fetchall()
        if quickCheck != [("ok",)]:
            raise RuntimeError("SQLCipher quick_check 失败")
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="发布 KMS + SQLCipher 加密资源")
    parser.add_argument(
        "--resource-key",
        dest="resourceKey",
        required=True,
        choices=("hskCorpus", "hskLocalCorpus"),
    )
    parser.add_argument("--version", required=True)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--force", action="store_true")
    arguments = parser.parse_args()

    sourcePath = arguments.input.resolve()
    outputPath = arguments.output.resolve()
    if not sourcePath.is_file():
        parser.error("--input 必须是存在的 SQLite 文件")
    if sourcePath == outputPath:
        parser.error("--output 不能覆盖输入文件")
    if outputPath.exists() and not arguments.force:
        parser.error("输出文件已存在；确认后使用 --force")

    outputPath.parent.mkdir(parents=True, exist_ok=True)
    if outputPath.exists():
        outputPath.unlink()
    try:
        dataKey, wrappedKey = generateResourceDataKey(
            arguments.resourceKey,
            arguments.version,
        )
        encryptDatabase(sourcePath, outputPath, dataKey)
    except Exception:
        outputPath.unlink(missing_ok=True)
        raise

    prefix = "HSK_CORPUS" if arguments.resourceKey == "hskCorpus" else "HSK_LOCAL_CORPUS"
    print(f"{prefix}_SHA256={_sha256(outputPath)}")
    print(f"{prefix}_VERSION={arguments.version}")
    print(f"{prefix}_KMS_WRAPPED_KEY={wrappedKey}")
    print("已生成 SQLCipher 4 加密资源；未输出明文数据库密钥。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
