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


def _rm(svc) -> RMClient:
    return RMClient(svc.s, svc.http)


def _view(v: dict, tenant_id: str, apps_domain: str) -> dict:
    exp = v.get("exposures") or []
    return {
        "state": v.get("state") or v.get("workspace_state") or v.get("status"),
        "workspace_enabled": v.get("workspace_enabled"),
        "apps": [{"app": e.get("app"), "port": e.get("port"),
                  "url": f"https://{e.get('app')}-{tenant_id}.{apps_domain}"} for e in exp],
        "usage": v.get("usage"),
        "last_backup_at": v.get("last_backup_at"),
    }


async def status(svc, ctx: RunContext, args: dict):
    v = await _rm(svc).call("GET", f"/v1/tenants/{ctx.tenant_id}", scope=ctx.tenant_id)
    return _view(v, ctx.tenant_id, svc.s.apps_base_domain)


async def start(svc, ctx: RunContext, args: dict):
    v = await _rm(svc).call("POST", f"/v1/tenants/{ctx.tenant_id}/workspace:start",
                            scope=ctx.tenant_id, body={}, timeout=120.0)
    return _view(v, ctx.tenant_id, svc.s.apps_base_domain)


async def stop(svc, ctx: RunContext, args: dict):
    await svc.confirm(ctx, kind="env_stop", risk="medium",
                      summary="Parar o ambiente da empresa (os apps publicados ficam fora do ar "
                              "até ele ser iniciado de novo)")
    v = await _rm(svc).call("POST", f"/v1/tenants/{ctx.tenant_id}/workspace:stop",
                            scope=ctx.tenant_id, body={}, timeout=60.0)
    return _view(v, ctx.tenant_id, svc.s.apps_base_domain)


async def backup(svc, ctx: RunContext, args: dict):
    r = await _rm(svc).call("POST", f"/v1/tenants/{ctx.tenant_id}/backup", scope=ctx.tenant_id,
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
    url = f"https://{app}-{ctx.tenant_id}.{svc.s.apps_base_domain}"
    await svc.confirm(ctx, kind="publish_app", risk="high",
                      summary=f"Publicar o app “{app}” (porta {port}) na internet em {url}",
                      details={"app": app, "port": port, "url": url})
    await _rm(svc).call("POST", f"/v1/tenants/{ctx.tenant_id}/exposures", scope=ctx.tenant_id,
                        body={"app": app, "port": port, "auth": "none"})
    return {"app": app, "port": port, "url": url}


async def unexpose(svc, ctx: RunContext, args: dict):
    app = str(args.get("app") or "").strip().lower()
    if not APP_RE.match(app):
        raise ToolError("invalid_args", "Nome de app inválido.")
    await svc.confirm(ctx, kind="unpublish_app", risk="medium",
                      summary=f"Tirar o app “{app}” da internet (o serviço continua rodando)")
    await _rm(svc).call("DELETE", f"/v1/tenants/{ctx.tenant_id}/exposures/{app}",
                        scope=ctx.tenant_id)
    return {"app": app, "published": False}


async def domain_add(svc, ctx: RunContext, args: dict):
    host = str(args.get("host") or "").strip().lower().rstrip(".")
    app = str(args.get("app") or "").strip().lower()
    if not HOST_RE.match(host) or not APP_RE.match(app):
        raise ToolError("invalid_args", "Informe um domínio válido e o nome do app publicado.")
    await svc.confirm(ctx, kind="domain_add", risk="high",
                      summary=f"Ligar o domínio {host} ao app “{app}”",
                      details={"host": host, "app": app})
    r = await _rm(svc).call("POST", f"/v1/tenants/{ctx.tenant_id}/domains", scope=ctx.tenant_id,
                            body={"host": host, "app": app})
    return {"host": r.get("host", host),
            "dns": [{"type": "TXT", "name": r.get("txt_name"), "value": r.get("txt_value")},
                    {"type": "CNAME", "name": host, "value": r.get("cname_target")}],
            "next": "Crie os dois registros no provedor de DNS e depois use rt_env_domain_verify."}


async def domain_verify(svc, ctx: RunContext, args: dict):
    host = str(args.get("host") or "").strip().lower().rstrip(".")
    if not HOST_RE.match(host):
        raise ToolError("invalid_args", "Informe o domínio.")
    r = await _rm(svc).call("POST", f"/v1/tenants/{ctx.tenant_id}/domains/{host}/verify",
                            scope=ctx.tenant_id, body={})
    return {"host": host, "active": bool(r.get("active")), "reason": r.get("reason", "")}
