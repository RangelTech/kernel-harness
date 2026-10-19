"""Gerador do perfil Hermes por (empresa, template, usuario) -- spec 04 5.4/5.5.

Entrada: tenant, template, user, manifesto (ferramentas, ambiente sim/nao, alias de modelo, limites).
Saida em disco (dentro de <hermes_home>/profiles/<profile_id>/):
    config.yaml            config do perfil (base.yaml + campos calculados)
    .env                   SO a API_SERVER_KEY do perfil (derivada por HMAC)
    .no-bundled-skills     marcador: nenhuma skill embutida e semeada
    plugins/rt-broker      symlink para o plugin da imagem (o Hermes carrega plugins POR PERFIL)
    .rt-profile.json       metadados + hash do config (regeneracao so se mudar)

Dependencias: stdlib + PyYAML. Idempotente: reescreve so se o hash do config mudou.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
BASE_TEMPLATE = HERE / "templates" / "base.yaml"

# Regex canonico do Hermes para nome de perfil (hermes_constants.PROFILE_ID_RE): max 64 chars.
PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
# ids de dominio (tenant/template/usuario): UUIDs ou slugs. Sem separadores de caminho.
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
TOOL_NAME_RE = re.compile(r"^rt_[a-z0-9_]{1,60}$")

NO_ENV_SSH_HOST = "ambiente-desligado.invalid"
PLUGIN_ID = "rt-broker"
PLATFORM_TOOLSET = "rt_platform"
CORE_TOOLSETS = ["skills", "memory", "session_search", "todo"]  # sempre presentes
OPTIONAL_TOOLSETS = {"web", "vision", "image_gen"}              # liberaveis pelo manifesto
# Nunca habilitaveis em worker (spec 5.5/5.8): tocam disco/shell/rede local do worker.
FORBIDDEN_TOOLSETS = {"terminal", "file", "code_execution", "browser", "cronjob", "delegation",
                      "tts", "kanban", "computer_use", "connections", "clarify"}
# terminal+file so existem quando o AMBIENTE da empresa esta habilitado (backend ssh).
ENV_TOOLSETS = ["terminal", "file"]

# Skills de sistema por grupo (pastas em <system_skills>/<grupo>): um agente so ve as skills dos grupos
# cujas ferramentas o manifesto liga (uma skill nunca amplia o que o agente pode fazer). `core` sempre.
SYSTEM_SKILL_GROUPS: dict[str, set[str] | None] = {
    "core": None,
    "exemplos": None,   # biblioteca de trechos de codigo de referencia; sempre ativa, nunca obrigatoria
    "pix": {"rt_pix_create", "rt_payment_status", "rt_mp_read"},
    "dados": {"rt_sql_read", "rt_sql_write", "rt_sql_transaction", "rt_data_describe", "rt_mongo_find"},
    "email": {"rt_email_send", "rt_email_list"},
    "docs": {"rt_search_docs", "rt_memory_recall"},
    "api": {"rt_http"},
    "analise": {"rt_executor_run"},
    "rotinas": {"rt_job_schedule", "rt_schedule_create", "rt_schedule_list", "rt_schedule_pause",
                "rt_schedule_delete"},
    "ambiente": {"rt_env_manage", "rt_env_status", "rt_env_start", "rt_env_stop", "rt_env_backup",
                 "rt_env_expose", "rt_env_unexpose", "rt_env_domain_add", "rt_env_domain_verify"},
}


def system_skill_dirs(system_root: str, tools: list[str], environment_enabled: bool) -> list[str]:
    have = set(tools)
    out = []
    for group, needs in SYSTEM_SKILL_GROUPS.items():
        on = needs is None or bool(have & needs) or (group == "ambiente" and environment_enabled)
        if on:
            out.append(f"{system_root.rstrip('/')}/{group}")
    return out
ALWAYS_DISABLED = ["cronjob", "delegation", "browser", "tts", "kanban", "computer_use", "connections"]


class ConfigError(ValueError):
    pass


@dataclass
class Limits:
    max_turns: int = 60
    turn_timeout_s: int = 540      # aplicado pelo dispatcher, nao pelo Hermes (informativo aqui)
    idle_timeout_s: int = 120      # idem
    max_concurrent_runs: int = 4


@dataclass
class EnvironmentSpec:
    enabled: bool = False
    ssh_host: str = ""
    ssh_user: str = "agent"
    ssh_port: int = 22
    ssh_key_path: str = ""         # caminho da chave NO WORKER (nunca o conteudo)


@dataclass
class Manifest:
    tools: list[str] = field(default_factory=list)            # ferramentas rt_* permitidas
    toolsets: list[str] = field(default_factory=list)         # extras: web, vision, image_gen
    environment: EnvironmentSpec = field(default_factory=EnvironmentSpec)
    model_alias: str = "agente-padrao"
    aux_model_alias: str = ""                                 # compressao; vazio = mesmo modelo
    reasoning_effort: str = ""
    limits: Limits = field(default_factory=Limits)
    learning: str = "review"   # review (padrao) | off | auto  (politica do tenant, spec 3.5)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Manifest:
        env = d.get("environment") or {}
        if isinstance(env, bool):
            env = {"enabled": env}
        lim = d.get("limits") or {}
        return cls(
            tools=list(d.get("tools") or []),
            toolsets=list(d.get("toolsets") or []),
            environment=EnvironmentSpec(**{k: v for k, v in env.items() if k in EnvironmentSpec.__dataclass_fields__}),
            model_alias=d.get("model_alias") or (d.get("model") or {}).get("alias") or "agente-padrao",
            aux_model_alias=d.get("aux_model_alias", ""),
            reasoning_effort=(d.get("model") or {}).get("reasoning_effort", d.get("reasoning_effort", "")),
            limits=Limits(**{k: v for k, v in lim.items() if k in Limits.__dataclass_fields__}),
            learning=d.get("learning", "review"),
        )


@dataclass
class Paths:
    """Caminhos DENTRO do worker (como o Hermes os enxerga)."""
    hermes_home: str = "/opt/data"
    system_skills: str = "/skills/system"
    tenant_skills_root: str = "/skills/tenant"
    users_root: str = "/srv/hermes/users"
    plugin_src: str = "/opt/rt/plugins/rt-broker"
    # URL de base do gateway de modelos (LiteLLM) vista pelo worker
    llm_base_url: str = "http://llm-gw:4000/v1"
    # comando que imprime a chave virtual do tenant (H3); vazio = usar a chave do .env/OPENAI_API_KEY
    llm_key_cmd: str = ""
    # nome da variavel de ambiente (do worker) com a chave virtual do gateway de modelos
    llm_key_env: str = "OPENAI_API_KEY"


def profile_id(tenant: str, template: str, user: str | None) -> str:
    """Id de perfil CURTO e deterministico. O nome <empresa>__<template>__<usuario> da spec estoura os
    64 chars do Hermes com UUIDs; o triplo fica em .rt-profile.json (e no registro do kernel)."""
    for label, v in (("tenant", tenant), ("template", template)):
        if not ID_RE.match(v or ""):
            raise ConfigError(f"{label} invalido: {v!r}")
    if user is not None and not ID_RE.match(user):
        raise ConfigError(f"user invalido: {user!r}")
    digest = hashlib.sha256(f"{tenant}\x1f{template}\x1f{user or '-'}".encode()).hexdigest()[:24]
    pid = ("u" if user else "s") + "-" + digest   # 'u-' perfil de usuario, 's-' perfil compartilhado do template
    assert PROFILE_ID_RE.match(pid)
    return pid


def derive_api_key(master_secret: str | bytes, pid: str) -> str:
    """Chave da API do perfil: HMAC-SHA256(master, 'rt-hermes-api-key:v1:'+pid) em hex (64 chars)."""
    if isinstance(master_secret, str):
        master_secret = master_secret.encode()
    if len(master_secret) < 16:
        raise ConfigError("master_secret curto demais (min 16 bytes)")
    return hmac.new(master_secret, f"rt-hermes-api-key:v1:{pid}".encode(), hashlib.sha256).hexdigest()


def session_id(tenant: str, user: str | None, chat: str) -> str:
    return f"{tenant}:{user or 'ext'}:{chat}"


def _deep_merge(a: dict, b: dict) -> dict:
    out = copy.deepcopy(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_base() -> dict:
    return yaml.safe_load(BASE_TEMPLATE.read_text(encoding="utf-8"))


def _validate_manifest(m: Manifest) -> None:
    for t in m.tools:
        if not TOOL_NAME_RE.match(t):
            raise ConfigError(f"ferramenta invalida no manifesto: {t!r}")
    for ts in m.toolsets:
        if ts in FORBIDDEN_TOOLSETS:
            raise ConfigError(f"toolset proibido no worker: {ts}")
        if ts not in OPTIONAL_TOOLSETS:
            raise ConfigError(f"toolset desconhecido: {ts} (permitidos: {sorted(OPTIONAL_TOOLSETS)})")
    if m.learning not in ("review", "off", "auto"):
        raise ConfigError("learning deve ser review|off|auto")
    if m.limits.max_turns < 1 or m.limits.max_turns > 500:
        raise ConfigError("max_turns fora de 1..500")
    e = m.environment
    if e.enabled and not (e.ssh_host and e.ssh_key_path):
        raise ConfigError("ambiente habilitado exige ssh_host e ssh_key_path")


def build_config(tenant: str, template: str, user: str | None, manifest: Manifest | dict,
                 paths: Paths | None = None, template_version: str = "current") -> dict:
    """Monta o dict do config.yaml do perfil (puro, sem I/O)."""
    paths = paths or Paths()
    m = manifest if isinstance(manifest, Manifest) else Manifest.from_dict(manifest)
    _validate_manifest(m)
    cfg = load_base()

    ext = system_skill_dirs(paths.system_skills, m.tools, m.environment.enabled)
    ext.append(f"{paths.tenant_skills_root}/{template}/{template_version}")
    if user:
        ext.append(f"{paths.users_root}/{tenant}/{user}/skills")
    ext = [p.rstrip("/") for p in ext]

    toolsets = list(CORE_TOOLSETS)
    if m.tools:
        toolsets.append(PLATFORM_TOOLSET)
    toolsets += [t for t in m.toolsets if t not in toolsets]
    if m.environment.enabled:
        toolsets += ENV_TOOLSETS
    disabled = [t for t in ALWAYS_DISABLED]
    if not m.environment.enabled:
        disabled += ["terminal", "code_execution", "file"]

    model: dict[str, Any] = {"default": m.model_alias, "base_url": paths.llm_base_url}
    if paths.llm_key_env:
        model["key_env"] = paths.llm_key_env
    aux_model = m.aux_model_alias or m.model_alias
    cfg = _deep_merge(cfg, {
        "model": model,
        "auxiliary": {"compression": {"base_url": paths.llm_base_url, "model": aux_model}},
        "platform_toolsets": {"api_server": toolsets},
        "agent": {"max_turns": m.limits.max_turns, "disabled_toolsets": disabled},
        "skills": {"external_dirs": ext},
        "plugins": {
            "enabled": [PLUGIN_ID] if m.tools else [],
            "entries": {PLUGIN_ID: {"settings": {"tools": sorted(set(m.tools))}}} if m.tools else {},
        },
    })
    if m.reasoning_effort:
        cfg["agent"]["reasoning_effort"] = m.reasoning_effort
    if paths.llm_key_cmd:
        cfg["providers"] = {"llm-gw": {"base_url": paths.llm_base_url, "key_cmd": paths.llm_key_cmd}}

    # aprendizado (politica do tenant, spec 3.5)
    if m.learning == "off":
        cfg["auxiliary"]["background_review"]["enabled"] = False
        cfg["skills"]["creation_nudge_interval"] = 0
        cfg["memory"]["nudge_interval"] = 0
    elif m.learning == "review":
        cfg["auxiliary"]["background_review"]["enabled"] = True
        cfg["skills"]["creation_nudge_interval"] = 10
        cfg["skills"]["write_approval"] = True
    elif m.learning == "auto":
        cfg["auxiliary"]["background_review"]["enabled"] = True
        cfg["skills"]["creation_nudge_interval"] = 10
        cfg["skills"]["write_approval"] = True   # o promotor do kernel aprova sozinho apos o scanner

    if m.environment.enabled:
        e = m.environment
        cfg["terminal"] = {"backend": "ssh", "ssh_host": e.ssh_host, "ssh_user": e.ssh_user,
                           "ssh_port": e.ssh_port, "ssh_key": e.ssh_key_path, "persistent_shell": True}
    else:
        # Sem ambiente: terminal/file/code_execution estao fora dos toolsets, MAS o backend precisa ser
        # NAO-local. O Hermes confina vision_analyze (e o resolvedor de midia) a caches de midia do
        # HERMES_HOME so quando terminal.backend != local (tools/image_source.py, GHSA-gpxw-6wxv-w3qq);
        # com backend local, vision_analyze le QUALQUER caminho do worker (achado H1/B16). O host
        # sentinela .invalid nunca resolve: qualquer tentativa de sandbox falha fechada.
        cfg["terminal"] = {"backend": "ssh", "ssh_host": NO_ENV_SSH_HOST, "ssh_user": "none",
                           "ssh_port": 22, "persistent_shell": False}
    return cfg


def config_text(cfg: dict) -> str:
    return "# GERADO por kernel-harness/profiles/profile_config.py -- nao editar a mao\n" + yaml.safe_dump(
        cfg, sort_keys=True, allow_unicode=True, default_flow_style=False)


def _atomic_write(path: Path, data: str, mode: int = 0o644) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(data, encoding="utf-8", newline="\n")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def write_profile(profiles_root: str | Path, tenant: str, template: str, user: str | None,
                  manifest: Manifest | dict, master_secret: str | bytes, paths: Paths | None = None,
                  template_version: str = "current", uid: int | None = None, gid: int | None = None,
                  llm_api_key: str = "") -> dict:
    """Cria/atualiza o perfil em disco. Devolve {profile_id, api_key, path, changed}.

    O perfil "nasce" (3 arquivos pequenos + symlink) na primeira chamada; nas seguintes so reescreve se o
    hash do config mudou (custo: ler 1 json + hashear). A api_key e SEMPRE rederivada (nao e guardada
    em lugar nenhum alem do .env do proprio perfil).
    """
    paths = paths or Paths()
    m = manifest if isinstance(manifest, Manifest) else Manifest.from_dict(manifest)
    pid = profile_id(tenant, template, user)
    cfg = build_config(tenant, template, user, m, paths, template_version)
    text = config_text(cfg)
    sha = hashlib.sha256(text.encode()).hexdigest()
    key = derive_api_key(master_secret, pid)
    root = Path(profiles_root) / pid
    meta_path = root / ".rt-profile.json"
    changed = True
    if meta_path.is_file():
        try:
            changed = json.loads(meta_path.read_text(encoding="utf-8")).get("config_sha256") != sha
        except Exception:
            changed = True
    env_text = f"API_SERVER_KEY={key}\n"
    if llm_api_key and paths.llm_key_env:
        # sob multiplex o Hermes le segredos SO do .env do perfil (nunca de os.environ)
        env_text += f"{paths.llm_key_env}={llm_api_key}\n"
    env_ok = (root / ".env").is_file() and (root / ".env").read_text(encoding="utf-8") == env_text
    if changed or not env_ok or not (root / "config.yaml").is_file():
        root.mkdir(parents=True, exist_ok=True)
        (root / "plugins").mkdir(exist_ok=True)
        link = root / "plugins" / PLUGIN_ID
        if m.tools:
            if link.is_symlink() or link.exists():
                if not link.is_symlink() or os.readlink(link) != paths.plugin_src:
                    link.unlink()
                    os.symlink(paths.plugin_src, link)
            else:
                os.symlink(paths.plugin_src, link)
        elif link.is_symlink():
            link.unlink()
        (root / ".no-bundled-skills").touch()
        # .env so com a chave da API do perfil (nenhum segredo de terceiro)
        _atomic_write(root / ".env", env_text, 0o600)
        # identidade do perfil por ultimo: config.yaml e o marcador que faz o gateway "enxergar" o perfil
        _atomic_write(meta_path, json.dumps({
            "tenant": tenant, "template": template, "user": user, "profile_id": pid,
            "template_version": template_version, "config_sha256": sha, "contract": 1}, sort_keys=True), 0o644)
        _atomic_write(root / "config.yaml", text, 0o644)
        if uid is not None:
            for p in [root, *root.rglob("*")]:
                try:
                    os.chown(p, uid, gid if gid is not None else uid, follow_symlinks=False)
                except (PermissionError, FileNotFoundError):
                    pass
    if user:
        udir = Path(paths.users_root) / tenant / user / "skills"
        # a pasta do usuario e criada pelo promotor; aqui so se o caminho for gravavel (dev/POC)
        try:
            udir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return {"profile_id": pid, "api_key": key, "path": str(root), "changed": changed}


def publish_tenant_version(tenant_skills_root: str | Path, template: str, version: str) -> None:
    """Troca atomica do link <root>/<template>/current -> <version> (a pasta da versao ja materializada)."""
    base = Path(tenant_skills_root) / template
    target = base / version
    if not target.is_dir():
        raise ConfigError(f"versao nao materializada: {target}")
    tmp = base / f".current.{os.getpid()}"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(version, tmp)       # link relativo
    os.replace(tmp, base / "current")


def _main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Gera o perfil Hermes (config.yaml, .env, plugin, marcadores)")
    ap.add_argument("--profiles-root", required=True)
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--template", required=True)
    ap.add_argument("--user")
    ap.add_argument("--manifest", required=True, help="arquivo JSON do manifesto")
    ap.add_argument("--master-secret-env", default="RT_PROFILE_MASTER_SECRET")
    ap.add_argument("--users-root", default=Paths.users_root)
    ap.add_argument("--llm-base-url", default=Paths.llm_base_url)
    ap.add_argument("--llm-key-cmd", default="")
    ap.add_argument("--template-version", default="current")
    ap.add_argument("--uid", type=int)
    ap.add_argument("--print-key", action="store_true")
    a = ap.parse_args(argv)
    manifest = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    paths = Paths(users_root=a.users_root, llm_base_url=a.llm_base_url, llm_key_cmd=a.llm_key_cmd)
    res = write_profile(a.profiles_root, a.tenant, a.template, a.user, manifest, os.environ[a.master_secret_env],
                        paths, a.template_version, uid=a.uid)
    if not a.print_key:
        res["api_key"] = "***"
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
