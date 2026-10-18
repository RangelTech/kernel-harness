"""Autenticacao (bearer de servico, run_token HS256, tokens do broker) e redacao de segredos."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from typing import Any


class AuthError(Exception):
    def __init__(self, code: str, detail: str, status: int = 401):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign_run_token(claims: dict, secret: str) -> str:
    """HS256 minimo (o backend assina; aqui so existe para testes e dev)."""
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    sig = hmac.new(secret.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
    return f"{header}.{body}.{_b64url(sig)}"


def verify_run_token(token: str, secret: str, *, now: float | None = None) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError("bad_run_token", "run_token malformado", 401)
    header, body, sig = parts
    try:
        hdr = json.loads(_b64url_dec(header))
        if hdr.get("alg") != "HS256":
            raise AuthError("bad_run_token", "algoritmo do run_token nao suportado", 401)
        expected = hmac.new(secret.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64url_dec(sig)):
            raise AuthError("bad_run_token", "assinatura do run_token invalida", 401)
        claims = json.loads(_b64url_dec(body))
    except AuthError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AuthError("bad_run_token", "run_token ilegivel", 401) from exc
    exp = claims.get("exp")
    if exp is None or (now or time.time()) >= float(exp):
        raise AuthError("bad_run_token", "run_token expirado", 401)
    return claims


def check_run_token_claims(claims: dict, body: dict) -> None:
    """Os claims precisam casar com a run: um token de outra run/tenant e recusado."""
    if claims.get("run_id") != body["run_id"]:
        raise AuthError("bad_run_token", "run_token de outra run", 403)
    if claims.get("tenant_id") != body["tenant_id"]:
        raise AuthError("bad_run_token", "run_token de outro tenant", 403)
    if claims.get("user_id") not in (None, body.get("user_id")):
        raise AuthError("bad_run_token", "run_token de outro usuario", 403)


def bearer(header: str | None) -> str:
    if not header or not header.lower().startswith("bearer "):
        return ""
    return header[7:].strip()


def token_in(token: str, allowed: list[str]) -> bool:
    ok = False
    for a in allowed:
        if a and hmac.compare_digest(a.encode(), token.encode()):
            ok = True
    return ok


# ---------------------------------------------------------------- redacao

_SECRET_KEY = re.compile(
    r"(secret|passw|senha|token|api[_-]?key|apikey|private|credential|authorization|"
    r"access_key|client_secret|refresh)", re.IGNORECASE)
_MIN_SECRET_LEN = 6


def collect_secret_values(creds: Any) -> set[str]:
    """Valores de credencial a mascarar em qualquer saida (SSE, broker, logs)."""
    found: set[str] = set()

    def walk(node: Any, secretish: bool) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                # `secrets{}` e `secret` (datasources) inteiros contam como segredo
                walk(v, secretish or k in ("secrets", "secret") or bool(_SECRET_KEY.search(str(k))))
        elif isinstance(node, list):
            for v in node:
                walk(v, secretish)
        elif isinstance(node, str) and secretish and len(node) >= _MIN_SECRET_LEN:
            found.add(node)

    walk(creds, False)
    return found


@dataclass
class Redactor:
    values: list[str]

    @classmethod
    def from_credentials(cls, creds: Any) -> Redactor:
        # mais longos primeiro: evita deixar sufixo de um segredo contido em outro
        return cls(sorted(collect_secret_values(creds), key=len, reverse=True))

    def scrub(self, text: str) -> str:
        for v in self.values:
            if v in text:
                text = text.replace(v, "[REDACTED]")
        return text

    def scrub_obj(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.scrub(obj)
        if isinstance(obj, dict):
            return {k: self.scrub_obj(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.scrub_obj(v) for v in obj]
        return obj

    def clear(self) -> None:
        self.values.clear()
