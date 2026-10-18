"""Cliente do runtime-manager (RM) para o broker: assinatura HMAC por requisicao (anexo runtime 1.3).

Ferramentas de ambiente falam com o RM com escopo da EMPRESA da run (nunca de outra); o executor
efemero usa escopo '*' (servico admin). O agente nunca ve segredo nem URL do RM.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

import httpx

from broker.errors import ToolError
from harness.config import Settings

_MESSAGES = {
    "workspace_not_enabled": "O ambiente da empresa não está habilitado.",
    "tenant_suspended": "O ambiente está suspenso. Fale com o administrador da plataforma.",
    "app_not_published": "Esse app ainda não foi publicado.",
    "invalid_app": "Nome de app inválido (use minúsculas, números e hífen).",
    "invalid_port": "Porta inválida (use de 1024 a 65535).",
    "invalid_domain": "Domínio inválido.",
    "domain_in_use": "Esse domínio já está em uso por outra empresa.",
    "domain_not_found": "Domínio não encontrado.",
    "unknown_tenant": "O ambiente da empresa ainda não foi criado.",
    "script_too_large": "O script é grande demais.",
    "inputs_too_large": "Os arquivos de entrada são grandes demais.",
}


def sign(secret: str, service: str, scope: str, method: str, target: str, body: bytes,
         iat: int | None = None) -> dict[str, str]:
    iat_s = str(int(time.time()) if iat is None else iat)
    msg = "\n".join([service, iat_s, scope, method.upper(), target,
                     hashlib.sha256(body).hexdigest()]).encode()
    sig = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
    return {"X-RM-Service": service, "X-RM-Iat": iat_s, "X-RM-Scope": scope, "X-RM-Signature": sig}


class RMClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        self.s = settings
        self.http = http

    @property
    def configured(self) -> bool:
        return bool(self.s.runtime_manager_url and self.s.rm_service and self.s.rm_secret)

    async def call(self, method: str, path: str, *, scope: str, body: dict | None = None,
                   idem: str | None = None, timeout: float = 30.0) -> dict:
        if not self.configured:
            raise ToolError("unavailable", "O ambiente da empresa não está disponível agora.")
        raw = json.dumps(body, separators=(",", ":")).encode() if body is not None else b""
        headers = sign(self.s.rm_secret, self.s.rm_service, scope, method, path, raw)
        headers["Content-Type"] = "application/json"
        headers["Idempotency-Key"] = idem or uuid.uuid4().hex
        try:
            r = await self.http.request(method, self.s.runtime_manager_url.rstrip("/") + path,
                                        content=raw, headers=headers, timeout=timeout)
        except httpx.HTTPError as exc:
            raise ToolError("unavailable", "O ambiente da empresa não está disponível agora.") \
                from exc
        if r.status_code >= 400:
            try:
                data = r.json()
            except ValueError:
                data = {}
            code = str(data.get("error") or data.get("code") or f"http_{r.status_code}")
            msg = _MESSAGES.get(code) or "O ambiente recusou a operação."
            raise ToolError("env_error" if r.status_code < 500 else "unavailable", msg)
        try:
            return r.json()
        except ValueError:
            return {}
