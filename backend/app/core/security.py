"""平台账号认证：密码哈希（pbkdf2）+ 签名 token（stdlib HMAC，零新增依赖）。

token 构成：`<base64url(payload)>.<hmac_sha256(payload, secret)>`，
payload 为 `uid:expires_at`，expires_at 是 UTC 秒级时间戳。校验时重算签名并
比对，同时检查有效期。不引入 itsdangerous/JWT，避免依赖膨胀。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

# token 有效期（天）
TOKEN_MAX_AGE_DAYS = 7
_ITERATIONS = 200_000
_SALT = "flame-auth-token"


def _sign(secret_key: str, payload: bytes) -> str:
    digest = hmac.new(
        (secret_key + _SALT).encode("utf-8"), payload, hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii")


def hash_password(password: str) -> str:
    """返回 `pbkdf2$iterations$salt$hash` 格式，salt 随机。"""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("ascii"), _ITERATIONS
    )
    return f"pbkdf2${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations_s, salt, expected = stored.split("$")
        iterations = int(iterations_s)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("ascii"), iterations
        )
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


def create_token(user_id: str, secret_key: str) -> str:
    expires_at = int(time.time()) + TOKEN_MAX_AGE_DAYS * 86400
    payload = f"{user_id}:{expires_at}".encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signature = _sign(secret_key, payload)
    return f"{encoded}.{signature}"


def decode_token(token: str, secret_key: str) -> str | None:
    """解出 user_id；过期或非法返回 None。"""
    try:
        encoded, signature = token.split(".", 1)
        padding = "=" * (-len(encoded) % 4)
        payload = base64.urlsafe_b64decode(encoded + padding)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(_sign(secret_key, payload), signature):
        return None
    try:
        user_id_s, expires_s = payload.decode("utf-8").split(":", 1)
    except ValueError:
        return None
    if int(expires_s) < int(time.time()):
        return None
    return user_id_s
