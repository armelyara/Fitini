"""
auth.py — minimal token authentication for the Fitini access gate.

This is demo-grade auth: one operator credential (from config.json, overridable with the
FITINI_PASSPHRASE env var), checked with a constant-time hash compare, issuing an opaque
in-memory bearer token with a TTL. It is intentionally small — enough to make the login
screen real — not a production identity system.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from typing import Any, Dict, Optional


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AuthManager:
    def __init__(self, config: Dict[str, Any]):
        auth = config.get("auth", {})
        self.operator_id = auth.get("operator_id", "OPERATOR")
        passphrase = os.environ.get("FITINI_PASSPHRASE", auth.get("passphrase", "fitini"))
        self.passphrase_hash = _sha256(passphrase)
        self.ttl = int(auth.get("token_ttl_seconds", 3600))
        # token -> expiry epoch
        self._tokens: Dict[str, float] = {}

    def login(self, operator_id: str, passphrase: str) -> Optional[str]:
        """Return a bearer token on success, else None."""
        ok_pass = hmac.compare_digest(self.passphrase_hash, _sha256(passphrase or ""))
        # Operator id is accepted loosely (demo): must be non-empty and match prefix.
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
