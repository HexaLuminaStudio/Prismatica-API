# coding: utf-8
"""密码哈希工具(本期预留,二期启用密码登录)。

- bcrypt cost >= 12(对齐 PRD §11 安全要求)
- > 72 字节先 SHA-256 截断(bcrypt 限制)
"""
from __future__ import annotations

import hashlib

import bcrypt


def hashPassword(plainPassword: str) -> str:
    """bcrypt 哈希密码(cost 默认 12)。"""
    raw = plainPassword.encode("utf-8")
    if len(raw) > 72:
        raw = hashlib.sha256(raw).digest()
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(raw, salt).decode("utf-8")


def verifyPassword(plainPassword: str, hashedPassword: str) -> bool:
    """校验密码(失败不抛异常)。"""
    try:
        raw = plainPassword.encode("utf-8")
        if len(raw) > 72:
            raw = hashlib.sha256(raw).digest()
        return bcrypt.checkpw(raw, hashedPassword.encode("utf-8"))
    except Exception:
        return False