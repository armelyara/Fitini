"""
auth.py — token authentication for the Fitini access gate.

One operator credential. The passphrase comes from the FITINI_PASSPHRASE env var when
set, otherwise from config.json. It is verified with a salted PBKDF2-HMAC-SHA256 hash
(per-process random salt) and a constant-time compare, issuing an opaque in-memory
bearer token with a TTL. Running on the shipped default is reported so the server can
warn the operator before the app is exposed.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from typing import Any, Dict, Optional

_DEFAULT_PASSPHRASE = "fitini"
_PBKDF2_ROUNDS = 200_000


def _derive(passphrase: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", (passphrase or "").encode("utf-8"), salt, _PBKDF2_ROUNDS)


class AuthManager:
    def __init__(self, config: Dict[str, Any]):
        auth = config.get("auth", {})
        self.operator_id = os.environ.get("FITINI_OPERATOR", auth.get("operator_id", "OPERATOR"))
        env_pass = os.environ.get("FITINI_PASSPHRASE")
        config_pass = auth.get("passphrase", _DEFAULT_PASSPHRASE)
        passphrase = env_pass if env_pass is not None else config_pass
        # True when no FITINI_PASSPHRASE is set and config still ships the default.
        self.using_default_credentials = env_pass is None and config_pass == _DEFAULT_PASSPHRASE
        self._salt = secrets.token_bytes(16)
        self._hash = _derive(passphrase, self._salt)
        self.ttl = int(auth.get("token_ttl_seconds", 3600))
        self._tokens: Dict[str, float] = {}

    def login(self, operator_id: str, passphrase: str) -> Optional[str]:
        """Return a bearer token on success, else None."""
        ok_pass = hmac.compare_digest(self._hash, _derive(passphrase or "", self._salt))
        ok_op = bool(operator_id) and operator_id.strip().upper().startswith(
            self.operator_id.split("//")[0].strip().upper()
        )
        if ok_pass and ok_op:
            token = secrets.token_urlsafe(32)
            self._tokens[token] = time.time() + self.ttl
            return token
        return None

    def validate(self, token: Optional[str]) -> bool:
        if not token:
            return False
        expiry = self._tokens.get(token)
        if expiry is None:
            return False
        if time.time() > expiry:
            self._tokens.pop(token, None)
            return False
        return True

    def logout(self, token: Optional[str]) -> None:
        if token:
            self._tokens.pop(token, None)
