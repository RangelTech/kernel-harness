"""Ferramentas rt_env_* (spec 04 7/9): operar o ambiente da empresa pelo runtime-manager.

Publicar, despublicar e ligar dominio exigem confirmacao autenticada (spec 6.5).
Todas operam SO no ambiente da empresa da run (escopo = tenant_id).
"""
from __future__ import annotations

import re

from broker.errors import ToolError
from broker.rm import RMClient
from harness.runctx import RunContext

APP_RE = re.compile(r"^[a-z][a-z0-9-]{0,30}$")
HOST_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")


def rm_tenant(tenant_id: str) -> str:
    """Id do tenant no runtime-manager: [a-z][a-z0-9]{3,19}. Derivado do UUID da empresa."""
    return "t" + re.sub(r"[^0-9a-f]", "", str(tenant_id).lower())[:12]


def _rm(svc) -> RMClient:
    return RMClient(svc.s, svc.http)


def _view(v: dict, rmt: str, apps_domain: str) -> dict:
    exp = v.get("exposures") or []
    obs = ((v.get("observed") or {}).get("workspace") or {}).get("state")
    state = {"running": "running", "exited": "stopped", "created": "stopped",
             "absent": "stopped", "paused": "degraded", "restarting": "degraded"}.get(obs, obs)
    if v.get("suspended"):
        state = "suspended"
    usage = (v.get("usage") or [None])[0]
    return {
        "state": state or "stopped",
        "workspace_enabled": v.get("workspace_enabled"),
        "apps": [{"app": e.get("app"), "port": e.get("port"),
                  "url": f"https://{e.get('app')}-{rmt}.{apps_domain}"} for e in exp],
        "usage": usage,
        "last_backup_at": v.get("last_backup_at"),
    }


async def _view_or_none(svc, rmt: str):
    try:
        return await _rm(svc).call("GET", f"/v1/tenants/{rmt}", scope=rmt)
    except ToolError as exc:
        if exc.code == "env_error":
            return None
        raise


async def _ensure(svc, rmt: str) -> dict:
    """Cria o ambiente da empresa no primeiro uso (escopo admin do kernel)."""
    v = await _view_or_none(svc, rmt)
    if v is not None:
        return v
    await _rm(svc).call("POST", f"/v1/tenants/{rmt}", scope="*",
                        body={"plan": "standard", "runtime": False, "workspace": True},
                        timeout=180.0)
    return await _rm(svc).call("GET", f"/v1/tenants/{rmt}", scope=rmt)


async def status(svc, ctx: RunContext, args: dict):
    rmt = rm_tenant(ctx.tenant_id)
    v = await _view_or_none(svc, rmt)
    if v is None:
        return {"state": "not_created", "apps": [],
                "note": "O ambiente ainda nao foi criado; use rt_env_start."}
    return _view(v, rmt, svc.s.apps_base_domain)


async def start(svc, ctx: RunContext, args: dict):
    rmt = rm_tenant(ctx.tenant_id)
    await _ensure(svc, rmt)
    await _rm(svc).call("POST", f"/v1/tenants/{rmt}/workspace:start", scope=rmt, body={},
                        timeout=180.0)
    v = await _rm(svc).call("GET", f"/v1/tenants/{rmt}", scope=rmt)
    return _view(v, rmt, svc.s.apps_base_domain)


async def exec_(svc, ctx: RunContext, args: dict):
    """Comando no terminal do ambiente da empresa (o agente nunca recebe chaves de acesso)."""
    command = str(args.get("command") or "")
    if not command.strip():
        raise ToolError("invalid_args", "Informe o comando.")
    try:
        timeout = max(1, min(int(args.get("timeout_s") or 60), 600))
    except (TypeError, ValueError):
        timeout = 60
    rmt = rm_tenant(ctx.tenant_id)
    res = await _rm(svc).call(
        "POST", f"/v1/tenants/{rmt}/workspace:exec", scope=rmt, timeout=timeout + 30.0,
        body={"command": command, "timeout_s": timeout, "cwd": str(args.get("cwd") or "/workspace")})
    out = {"exit_code": res.get("exit_code"), "stdout": res.get("stdout", ""),
           "stderr": res.get("stderr", "")}
    if res.get("exit_code") not in (0, None):
        out["hint"] = "O comando falhou; leia o stderr e corrija antes de repetir."
    return out


async def stop(svc, ctx: RunContext, args: dict):
    await svc.confirm(ctx, kind="env_stop", risk="medium",
                      summary="Parar o ambiente da empresa (os apps publicados ficam fora do ar "
                              "até ele ser iniciado de novo)")
    rmt = rm_tenant(ctx.tenant_id)
    await _rm(svc).call("POST", f"/v1/tenants/{rmt}/workspace:stop", scope=rmt, body={},
                        timeout=60.0)
    v = await _rm(svc).call("GET", f"/v1/tenants/{rmt}", scope=rmt)
    return _view(v, rmt, svc.s.apps_base_domain)


async def backup(svc, ctx: RunContext, args: dict):
    rmt = rm_tenant(ctx.tenant_id)
    r = await _rm(svc).call("POST", f"/v1/tenants/{rmt}/backup", scope=rmt,
                            body={"kind": "manual"}, timeout=300.0)
    return {"backup_id": r.get("backup_id") or r.get("id"), "ok": True}


async def expose(svc, ctx: RunContext, args: dict):
    app = str(args.get("app") or "").strip().lower()
    try:
        port = int(args.get("port"))
    except (TypeError, ValueError):
        raise ToolError("invalid_args", "Informe a porta do serviço.") from None
    if not APP_RE.match(app):
        raise ToolError("invalid_args", "Nome de app inválido (use minúsculas, números e hífen).")
    if not 1024 <= port <= 65535:
        raise ToolError("invalid_args", "Porta inválida (use de 1024 a 65535).")
    rmt = rm_tenant(ctx.tenant_id)
    url = f"https://{app}-{rmt}.{svc.s.apps_base_domain}"
    await svc.confirm(ctx, kind="publish_app", risk="high",
                      summary=f"Publicar o app “{app}” (porta {port}) na internet em {url}",
                      details={"app": app, "port": port, "url": url})
    await _rm(svc).call("POST", f"/v1/tenants/{rmt}/exposures", scope=rmt,
                        body={"app": app, "port": port, "auth": "none"})
    return {"app": app, "port": port, "url": url}


async def unexpose(svc, ctx: RunContext, args: dict):
    app = str(args.get("app") or "").strip().lower()
    if not APP_RE.match(app):
        raise ToolError("invalid_args", "Nome de app inválido.")
    await svc.confirm(ctx, kind="unpublish_app", risk="medium",
                      summary=f"Tirar o app “{app}” da internet (o serviço continua rodando)")
    rmt = rm_tenant(ctx.tenant_id)
    await _rm(svc).call("DELETE", f"/v1/tenants/{rmt}/exposures/{app}", scope=rmt)
    return {"app": app, "published": False}


async def domain_add(svc, ctx: RunContext, args: dict):
    host = str(args.get("host") or "").strip().lower().rstrip(".")
    app = str(args.get("app") or "").strip().lower()
    if not HOST_RE.match(host) or not APP_RE.match(app):
        raise ToolError("invalid_args", "Informe um domínio válido e o nome do app publicado.")
    await svc.confirm(ctx, kind="domain_add", risk="high",
                      summary=f"Ligar o domínio {host} ao app “{app}”",
                      details={"host": host, "app": app})
    rmt = rm_tenant(ctx.tenant_id)
    r = await _rm(svc).call("POST", f"/v1/tenants/{rmt}/domains", scope=rmt,
                            body={"host": host, "app": app})
    return {"host": r.get("host", host),
            "dns": [{"type": "TXT", "name": r.get("txt_name"), "value": r.get("txt_value")},
                    {"type": "CNAME", "name": host, "value": r.get("cname_target")}],
            "next": "Crie os dois registros no provedor de DNS e depois use rt_env_domain_verify."}


async def domain_verify(svc, ctx: RunContext, args: dict):
    host = str(args.get("host") or "").strip().lower().rstrip(".")
    if not HOST_RE.match(host):
        raise ToolError("invalid_args", "Informe o domínio.")
    rmt = rm_tenant(ctx.tenant_id)
    r = await _rm(svc).call("POST", f"/v1/tenants/{rmt}/domains/{host}/verify", scope=rmt,
                            body={})
    return {"host": host, "active": bool(r.get("active")), "reason": r.get("reason", "")}
