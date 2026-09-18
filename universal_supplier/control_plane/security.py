from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time


ROLES = ("ADMIN", "OPERATOR", "VIEWER")
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,63}$")


@dataclass(frozen=True)
class AuthUser:
    id: int
    username: str
    display_name: str
    role: str
    is_active: bool = True


def normalize_username(value: str) -> str:
    value = value.strip().lower()
    if not USERNAME_RE.fullmatch(value):
        raise ValueError("Имя пользователя: 3–64 символа, латиница, цифры, точка, дефис или подчёркивание")
    return value


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    if len(password) < 12:
        raise ValueError("Пароль должен содержать не менее 12 символов")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()


def verify_password(password: str, encoded: str) -> bool:
    try:
        kind, n, r, p, salt64, digest64 = encoded.split("$", 5)
        if kind != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt64.encode())
        expected = base64.urlsafe_b64decode(digest64.encode())
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


class SessionSigner:
    cookie_name = "universal_supplier_session"

    def __init__(self, secret: str, *, ttl_seconds: int = 8 * 60 * 60) -> None:
        if len(secret.encode("utf-8")) < 32:
            raise ValueError("SESSION_SIGNING_SECRET must contain at least 32 bytes")
        self.secret = secret.encode("utf-8")
        self.ttl_seconds = ttl_seconds

    @classmethod
    def from_env(cls) -> "SessionSigner":
        secret = os.environ.get("SESSION_SIGNING_SECRET", "")
        if not secret:
            raise RuntimeError("SESSION_SIGNING_SECRET is required")
        return cls(secret)

    def issue(self, user: AuthUser) -> tuple[str, str]:
        csrf = secrets.token_urlsafe(24)
        payload = {"uid": user.id, "role": user.role, "csrf": csrf, "exp": int(time.time()) + self.ttl_seconds}
        raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=")
        signature = hmac.new(self.secret, raw, hashlib.sha256).digest()
        return (raw + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode(), csrf

    def read(self, token: str | None) -> dict | None:
        try:
            raw64, signature64 = (token or "").encode().split(b".", 1)
            expected = hmac.new(self.secret, raw64, hashlib.sha256).digest()
            actual = base64.urlsafe_b64decode(signature64 + b"=" * (-len(signature64) % 4))
            if base64.urlsafe_b64encode(actual).rstrip(b"=") != signature64:
                return None
            if not hmac.compare_digest(expected, actual):
                return None
            raw = base64.urlsafe_b64decode(raw64 + b"=" * (-len(raw64) % 4))
            payload = json.loads(raw)
            if int(payload["exp"]) < int(time.time()):
                return None
            return payload
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None

    @staticmethod
    def csrf_matches(payload: dict | None, supplied: str | None) -> bool:
        return bool(payload and supplied and hmac.compare_digest(str(payload.get("csrf", "")), supplied))


def role_allows(role: str, allowed: set[str]) -> bool:
    return role in allowed
