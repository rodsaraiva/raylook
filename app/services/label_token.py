"""Token assinado (HMAC-SHA256) que autoriza marcar um cliente como enviado via
QR da etiqueta. Mesmo esquema do auth_service: b64url(payload).b64url(sig).
Sem expiração — a etiqueta impressa é usada por dias e a única ação possível é
marcar envio de um cliente já pago."""
from __future__ import annotations

import base64
import hmac
import json
import os
from hashlib import sha256
from typing import Optional, Tuple


def _secret() -> bytes:
    env = os.getenv("LABEL_QR_SECRET", "").encode("utf-8")
    if env:
        return env
    from app.services.auth_service import _secret as _auth_secret
    return _auth_secret()


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def make_ship_token(pacote_id: str, cliente_id: str) -> str:
    payload = json.dumps({"p": pacote_id, "c": cliente_id}, separators=(",", ":")).encode()
    body = _b64url_encode(payload)
    sig = hmac.new(_secret(), body.encode(), sha256).digest()
    return f"{body}.{_b64url_encode(sig)}"


def read_ship_token(token: str) -> Optional[Tuple[str, str]]:
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
    p, c = data.get("p"), data.get("c")
    if not p or not c:
        return None
    return (str(p), str(c))
