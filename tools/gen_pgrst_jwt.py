"""Gera o JWT que o app usa pra autenticar no PostgREST.

Uso:
    python tools/gen_pgrst_jwt.py <PGRST_JWT_SECRET> [role]

Default role = raylook_api. Cole o output no .env como
SUPABASE_SERVICE_ROLE_KEY (e SUPABASE_ANON_KEY se quiser separar
permissões depois).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def gen_jwt(secret: str, role: str = "raylook_api") -> str:
    header = b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = b64url(json.dumps({"role": role}, separators=(",", ":")).encode())
    msg = f"{header}.{payload}".encode()
    sig = b64url(hmac.new(secret.encode(), msg, hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("uso: python tools/gen_pgrst_jwt.py <PGRST_JWT_SECRET> [role]", file=sys.stderr)
        sys.exit(1)
    secret = sys.argv[1]
    role = sys.argv[2] if len(sys.argv) > 2 else "raylook_api"
    print(gen_jwt(secret, role))
