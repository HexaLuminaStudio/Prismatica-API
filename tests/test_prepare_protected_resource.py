"""SQLCipher 资源发布脚本测试。"""

from __future__ import annotations

import os
import sqlite3

from sqlcipher3 import dbapi2 as sqlcipher

from scripts.prepare_protected_resource import encryptDatabase


def testEncryptDatabaseProducesReadableSqlCipherFile(tmp_path) -> None:
    sourcePath = tmp_path / "plain.db"
    outputPath = tmp_path / "encrypted.db"
    sourceConnection = sqlite3.connect(sourcePath)
    sourceConnection.execute("CREATE TABLE hsk_corpus(value TEXT)")
    sourceConnection.execute("INSERT INTO hsk_corpus(value) VALUES ('test')")
    sourceConnection.commit()
    sourceConnection.close()
    databaseKey = os.urandom(32)

    encryptDatabase(sourcePath, outputPath, databaseKey)

    assert outputPath.read_bytes()[:16] != b"SQLite format 3\x00"
    connection = sqlcipher.connect(outputPath)
    try:
        connection.execute(f"PRAGMA key = \"x'{databaseKey.hex()}'\"")
        connection.execute("PRAGMA cipher_compatibility = 4")
        assert connection.execute("SELECT value FROM hsk_corpus").fetchone() == (
            "test",
        )
        assert connection.execute("PRAGMA cipher_integrity_check").fetchall() == []
    finally:
        connection.close()
