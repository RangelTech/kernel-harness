"""rt_http: HTTP a APIs de terceiros com allowlist de hosts por tenant e {{secret:NOME}}
resolvido AQUI (o agente nunca ve o valor). Corrige o SSRF aberto do `call_http_api` antigo."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from urllib.parse import urlsplit

import httpx

from broker.errors import ToolError
from harness.runctx import RunContext

_PLACEHOLDER = re.compile(r"\{\{\s*secret:([A-Za-z0-9_.-]+)\s*\}\}")
METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"}
READ_METHODS = {"GET", "HEAD"}
MAX_REQ_BODY = 256 * 1024
BLOCKED_HEADERS = {"host", "content-length", "transfer-encoding", "connection"}


async def default_resolver(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({i[4][0] for i in infos})


def host_allowed(host: str, allowed: list[str]) -> bool:
    host = host.lower().rstrip(".")
    for a in allowed:
        a = a.lower().strip().rstrip(".")
        if not a:
            continue
        if a.startswith("*."):
            if host.endswith(a[1:]) and host != a[2:]:
                return True
        elif host == a:
            return True
    return False


def ip_forbidden(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return True
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    # `not is_global` tambem barra 100.64.0.0/10 (CGNAT/Tailscale), 192.0.0.0/24, 198.18.0.0/15 e blocos
    # de documentacao, que `is_private` sozinho deixa passar.
    return (not addr.is_global or addr.is_private or addr.is_loopback or addr.is_link_local
            or addr.is_multicast or addr.is_reserved or addr.is_unspecified)


def _resolve_text(text: str, secrets: dict, used: list[str]) -> str:
    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name not in secrets:
            raise ToolError("invalid_args", f"Segredo '{name}' não existe.")
        used.append(name)
        return str(secrets[name])

    return _PLACEHOLDER.sub(repl, text)


def _resolve_obj(obj, secrets, used):
    if isinstance(obj, str):
        return _resolve_text(obj, secrets, used)
    if isinstance(obj, dict):
        return {k: _resolve_obj(v, secrets, used) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_obj(v, secrets, used) for v in obj]
    return obj


async def call(svc, ctx: RunContext, args: dict):
    method = str(args.get("method") or "GET").upper()
    if method not in METHODS:
        raise ToolError("invalid_args", "Método HTTP não permitido.")
    url = str(args.get("url") or "")
    if _PLACEHOLDER.search(urlsplit(url).netloc):
        raise ToolError("guardrail", "Segredos não podem ser usados no endereço do servidor.")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ToolError("invalid_args", "URL inválida (use http ou https).")
    if parts.username or parts.password:
        raise ToolError("guardrail", "URL com credenciais embutidas não é permitida.")
    host = parts.hostname
    allowed = list(ctx.limits.get("http_allowed_hosts") or []) + list(
        svc.s.http_allowed_hosts_default)
    if allowed and not host_allowed(host, allowed):
        raise ToolError("forbidden_target", f"O host '{host}' não está liberado para este agente.")

    # destino resolvido e checado (IP privado/metadata/loopback sao proibidos)
    if _is_ip_literal(host):
        ips = [host]
    else:
        try:
            ips = await svc.resolver(host)
        except OSError as exc:
            raise ToolError("unavailable", "Não foi possível resolver o endereço.") from exc
    if not ips or any(ip_forbidden(ip) for ip in ips):
        raise ToolError("forbidden_target", "O endereço de destino não é permitido.")

    secrets = (ctx.credentials or {}).get("secrets") or {}
    used: list[str] = []
    if secrets and not ctx.granted("secrets"):
        secrets = {}
    final_url = _resolve_text(url, secrets, used)
    headers = _resolve_obj(args.get("headers") or {}, secrets, used)
    if not isinstance(headers, dict):
        raise ToolError("invalid_args", "headers deve ser um objeto.")
    headers = {str(k): str(v) for k, v in headers.items() if str(k).lower() not in BLOCKED_HEADERS
               and "\n" not in str(v) and "\r" not in str(v)}
    body = args.get("body")
    if isinstance(body, str) and body.strip()[:1] in "{[" :
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            pass
    body = _resolve_obj(body, secrets, used) if body is not None else None
    if len(json.dumps(body, default=str)) > MAX_REQ_BODY if body is not None else False:
        raise ToolError("limit", "Corpo da requisição grande demais.")

    if method not in READ_METHODS and ctx.limits.get("http_write_confirm", True):
        await svc.confirm(ctx, kind="http_write", risk="medium",
                          summary=f"Enviar {method} para {host}{parts.path or '/'}",
                          details={"method": method, "host": host, "path": parts.path})
    timeout = max(1.0, min(float(args.get("timeout_seconds") or 30), 60.0))
    kwargs: dict = {}
    if body is not None:
        kwargs["json" if not isinstance(body, str) else "content"] = body
    try:
        r = await svc.http.request(method, final_url, headers=headers, timeout=timeout,
                                   follow_redirects=False, **kwargs)
    except httpx.HTTPError as exc:
        raise ToolError("unavailable", f"A chamada falhou ({type(exc).__name__}).") from exc
    text = r.text[: svc.s.http_max_response_chars]
    return {"status": r.status_code, "body": text,
            "truncated": len(r.text) > svc.s.http_max_response_chars,
            "content_type": r.headers.get("content-type", "")}


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False
