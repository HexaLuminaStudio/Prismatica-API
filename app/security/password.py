"""密码策略与 bcrypt 哈希。

P0-A 统一要求：至少 10 个字符，同时包含字母和数字；bcrypt cost 固定为 12。
bcrypt 只处理前 72 字节，因此超长 UTF-8 密码先做 SHA-256 预哈希。
"""

from __future__ import annotations

import hashlib

import bcrypt

BCRYPT_ROUNDS = 12
MIN_PASSWORD_LENGTH = 10


def validatePassword(plainPassword: str) -> None:
    """校验密码策略，不符合时抛出 ValueError。"""
    if not isinstance(plainPassword, str):
        raise ValueError("密码必须是字符串")
    if len(plainPassword) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"密码至少需要 {MIN_PASSWORD_LENGTH} 位")
    if not any(char.isalpha() for char in plainPassword):
        raise ValueError("密码必须包含字母")
    if not any(char.isdigit() for char in plainPassword):
        raise ValueError("密码必须包含数字")


def _passwordBytes(plainPassword: str) -> bytes:
    raw = plainPassword.encode("utf-8")
    return hashlib.sha256(raw).digest() if len(raw) > 72 else raw


def hashPassword(plainPassword: str) -> str:
    """按统一策略校验并生成 bcrypt cost=12 哈希。"""
    validatePassword(plainPassword)
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(_passwordBytes(plainPassword), salt).decode("utf-8")


def verifyPassword(plainPassword: str, hashedPassword: str) -> bool:
    """校验密码；格式错误或损坏的哈希统一返回 False。"""
    try:
        return bcrypt.checkpw(_passwordBytes(plainPassword), hashedPassword.encode("utf-8"))
    except (AttributeError, TypeError, ValueError):
        return False


# P0-A 计划中的 snake_case 公共 API；保留 camelCase 供现有后台调用。
validate_password = validatePassword
hash_password = hashPassword
verify_password = verifyPassword

__all__ = [
    "BCRYPT_ROUNDS",
    "MIN_PASSWORD_LENGTH",
    "validatePassword",
    "hashPassword",
    "verifyPassword",
    "validate_password",
    "hash_password",
    "verify_password",
]
