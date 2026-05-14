"""Auth multi-usuário do dashboard interno (admin / estoque / logística).

3 usuários fixos via env (`RAYLOOK_USER_<ROLE>_HASH` com bcrypt).
Não há registro nem auto-reset — senha trocada manualmente via .env + redeploy.

Cookie assinado via HMAC-SHA256 (stdlib) carrega role + timestamp.
"""
from __future__ import annotations

import base64
import hmac
import json
import os
import time
from hashlib import sha256
from typing import Optional, Tuple

import bcrypt


ROLES = ("admin", "estoque", "logistica")
SESSION_MAX_AGE = 8 * 60 * 60  # 8h


def _hash_for(role: str) -> str:
    return (os.getenv(f"RAYLOOK_USER_{role.upper()}_HASH") or "").strip()


def _secret() -> bytes:
    return (os.getenv("SESSION_SECRET")
            or os.getenv("DASHBOARD_AUTH_PASS")
            or "dev-secret").encode("utf-8")


def verify_credentials(username: str, password: str) -> Optional[str]:
    """Retorna o role (== username válido) ou None."""
    username = (username or "").strip().lower()
    if username not in ROLES:
        return None
    pw_hash = _hash_for(username)
    if not pw_hash:
        return None
    try:
        if bcrypt.checkpw((password or "").encode("utf-8"), pw_hash.encode("utf-8")):
            return username
    except (ValueError, TypeError):
        return None
    return None


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def make_session_token(role: str) -> str:
    payload = json.dumps({"r": role, "t": int(time.time())}, separators=(",", ":")).encode()
    body = _b64url_encode(payload)
    sig = hmac.new(_secret(), body.encode(), sha256).digest()
    return f"{body}.{_b64url_encode(sig)}"


def read_session_token(token: str) -> Optional[str]:
    """Lê o cookie e devolve o role. None se expirado, inválido ou falsificado."""
    if not token or token.count(".") != 1:
        return None
    body, sig_b64 = token.split(".", 1)
    try:
        expected = hmac.new(_secret(), body.encode(), sha256).digest()
        provided = _b64url_decode(sig_b64)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(expected, provided):
        return None
    try:
        data = json.loads(_b64url_decode(body))
    except (ValueError, TypeError):
        return None
    issued = int(data.get("t") or 0)
    if issued <= 0 or time.time() - issued > SESSION_MAX_AGE:
        return None
    role = data.get("r")
    return role if role in ROLES else None


# RBAC ----------------------------------------------------------------------
# Estados sensíveis (regress/cancel/restore) são exclusivos do admin.
def can_advance(role: str, from_state: str, to_state: Optional[str] = None) -> bool:
    if role == "admin":
        return True
    target = (to_state or "").strip() or None
    if role == "estoque":
        if from_state == "pago" and target in (None, "pendente", "separado"):
            return True
        if from_state == "pendente" and target in (None, "separado"):
            return True
        return False
    if role == "logistica":
        if from_state == "separado" and target in (None, "enviado"):
            return True
        return False
    return False


def can_regress(role: str) -> bool:
    return role == "admin"


def can_cancel(role: str) -> bool:
    return role == "admin"


def can_restore(role: str) -> bool:
    return role == "admin"


def visible_groups(role: str) -> Tuple[str, ...]:
    """Quais dropdowns do rail o role enxerga (id usado em RAIL_GROUPS no JS)."""
    if role == "admin":
        return ("comercial", "estoque", "logistica", "financeiro")
    if role == "estoque":
        return ("estoque",)
    if role == "logistica":
        return ("logistica",)
    return ()
