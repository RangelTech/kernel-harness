"""Ferramentas rt_env_* (spec 04 7/9): operar o ambiente da empresa pelo runtime-manager.

Publicar, despublicar e ligar dominio exigem confirmacao autenticada (spec 6.5).
Todas operam SO no ambiente da empresa da run (escopo = tenant_id).
"""
from __future__ import annotations

import base64
import posixpath
import re
from pathlib import Path

from broker import attachments as attachments_mod
from broker.artifacts import put_artifact
from broker.errors import ToolError
from broker.executor import _KIND as KIND_BY_EXT
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


def available_secrets(ctx: RunContext) -> dict[str, str]:
    """Segredos que o codigo do agente pode ler como variavel de ambiente (nome -> valor)."""
    out: dict[str, str] = {}
    creds = ctx.credentials or {}
    if ctx.granted("secrets"):
        for name, value in (creds.get("secrets") or {}).items():
            out[re.sub(r"[^A-Za-z0-9_]", "_", str(name)).upper()] = str(value)
    luar = creds.get("luar") or {}  # acesso ao proprio Luar como o usuario do chat (H-6)
    if luar.get("token") and luar.get("url"):
        out["LUAR_URL"] = str(luar["url"])
        out["LUAR_CTX"] = str(luar["token"])
        out["LUAR_ACCOUNT_ID"] = str(luar.get("account_id") or "")
    pay = creds.get("payment") or {}
    if pay.get("access_token") and ctx.granted("mercado_pago"):
        out["MERCADO_PAGO_TOKEN"] = str(pay["access_token"])
    return out


def _command_env(ctx: RunContext, wanted) -> dict[str, str]:
    """Variaveis pedidas pelo agente (so os NOMES vem dele; o valor nunca passa pelo modelo)."""
    if not wanted:
        return {}
    have = available_secrets(ctx)
    names = [str(n).upper() for n in (wanted if isinstance(wanted, list) else [wanted])]
    missing = [n for n in names if n not in have]
    if missing:
        raise ToolError("invalid_args", "Segredo(s) nao cadastrado(s): " + ", ".join(missing))
    return {n: have[n] for n in names}


PACKAGE_DEST = "/workspace/pacote"
MAX_PACKAGE_UPLOAD_TOTAL = 100 * 1024 * 1024


def _package_files(svc, ctx: RunContext, wanted: str) -> list[tuple[str, Path]]:
    """Arquivos de `arquivos/` do pacote da VERSAO desta run (so leitura, sem sair da pasta)."""
    from harness.profiles import ASSETS_DIR, _seg, safe_relpath

    root = (Path(svc.s.tenant_skills_root) / _seg(ctx.template_id) / _seg(ctx.version_id)
            / ASSETS_DIR)
    if not root.is_dir():
        raise ToolError("unavailable", "Este agente nao tem arquivos no pacote do template.")
    base = root.resolve()
    if wanted in ("*", ASSETS_DIR, ASSETS_DIR + "/"):
        found = [p for p in sorted(base.rglob("*")) if p.is_file()]
    else:
        rel = safe_relpath(wanted)
        if rel is None or not rel.startswith(ASSETS_DIR + "/"):
            raise ToolError("invalid_args", "package_file deve ser 'arquivos/<nome>' ou '*'.")
        p = (base / rel[len(ASSETS_DIR) + 1:]).resolve()
        if base not in p.parents or not p.is_file():
            names = ", ".join(x.relative_to(base).as_posix() for x in sorted(base.rglob("*"))
                              if x.is_file()) or "nenhum"
            raise ToolError("invalid_args", f"Arquivo '{rel}' nao existe no pacote "
                                            f"(arquivos: {names}).")
        found = [p]
    out = [(x.relative_to(base).as_posix(), x) for x in found if base in x.resolve().parents]
    if sum(x.stat().st_size for _, x in out) > MAX_PACKAGE_UPLOAD_TOTAL:
        raise ToolError("limit", "Os arquivos do pacote passam de 100 MB; envie um a um.")
    return out


async def _upload_package(svc, ctx: RunContext, wanted: str, dest: str | None):
    """Arquivo(s) do pacote do template -> /workspace/pacote/ no ambiente da empresa."""
    files = _package_files(svc, ctx, wanted)
    rmt = rm_tenant(ctx.tenant_id)
    single = wanted not in ("*", "arquivos", "arquivos/")
    sent = []
    for rel, src in files:
        if single and dest and not dest.endswith("/"):
            path = dest
        else:
            path = posixpath.join(dest.rstrip("/") if dest else PACKAGE_DEST, rel)
        data = src.read_bytes()
        await _rm(svc).call("POST", f"/v1/tenants/{rmt}/workspace:put", scope=rmt, timeout=300.0,
                            body={"path": path, "content_base64": base64.b64encode(data).decode()})
        sent.append({"path": path, "size": len(data)})
    if single:
        return sent[0]
    return {"files": sent, "count": len(sent)}


async def upload(svc, ctx: RunContext, args: dict):
    """Anexo da conversa ou arquivo do pacote do template -> ambiente da empresa."""
    if args.get("package_file"):
        return await _upload_package(svc, ctx, str(args["package_file"]).strip(),
                                     str(args["path"]) if args.get("path") else None)
    name = str(args.get("attachment") or "")
    if not name:
        raise ToolError("invalid_args", "Informe `attachment` (anexo da conversa) ou "
                                        "`package_file` (arquivo do pacote do template).")
    data = await attachments_mod.read(svc, ctx, name)
    base = attachments_mod.find(ctx, name)["name"]
    path = str(args.get("path") or f"/workspace/inbox/{base}")
    rmt = rm_tenant(ctx.tenant_id)
    await _rm(svc).call("POST", f"/v1/tenants/{rmt}/workspace:put", scope=rmt, timeout=300.0,
                        body={"path": path, "content_base64": base64.b64encode(data).decode()})
    return {"path": path, "size": len(data)}


async def download(svc, ctx: RunContext, args: dict):
    """Arquivo do ambiente -> artefato da conversa (o usuario recebe o link/arquivo)."""
    path = str(args.get("path") or "")
    if not path.startswith("/"):
        raise ToolError("invalid_args", "Informe o caminho completo do arquivo no ambiente.")
    rmt = rm_tenant(ctx.tenant_id)
    res = await _rm(svc).call("POST", f"/v1/tenants/{rmt}/workspace:get", scope=rmt, timeout=300.0,
                              body={"path": path})
    data = base64.b64decode(res.get("content_base64") or "")
    base = posixpath.basename(path)
    ext = base.rsplit(".", 1)[-1].lower() if "." in base else "bin"
    kind, ctype = KIND_BY_EXT.get(ext, ("file", "application/octet-stream"))
    art = await put_artifact(svc, ctx, kind=kind, title=str(args.get("name") or base)[:120],
                             data=data, extension=ext[:8], content_type=ctype)
    return {"artifact": art, "size": len(data)}


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
    env = _command_env(ctx, args.get("secrets"))
    res = await _rm(svc).call(
        "POST", f"/v1/tenants/{rmt}/workspace:exec", scope=rmt, timeout=timeout + 30.0,
        body={"command": command, "timeout_s": timeout, "cwd": str(args.get("cwd") or "/workspace"),
              "env": env})
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
