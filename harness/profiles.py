"""Perfil Hermes por (empresa, template, usuario) + materializacao de skills em arquivos.

Interface com o WP-B (`profiles/profile_config.py`, branch feat/hermes-image):

    def generate(*, profile_name: str, profiles_root: str, tenant_id: str, template_id: str,
                 user_id: str | None, template_version_id: str, template_hash: str,
                 manifest: dict, model: dict, skills_dirs: dict, llm: dict) -> dict
        # skills_dirs = {"system": ..., "tenant": ..., "user": ... | None}
        # llm = {"base_url": ..., "key_cmd": ... | None}
        # retorna {"home": <HERMES_HOME do perfil>, "config_hash": <sha256 do config.yaml>}

Ate o merge usamos `StubProfileGenerator` (config minima valida, sem segredos). O kernel
so chama `generate` quando o hash do template difere do marcador `.rt_profile_hash`.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Protocol

from broker.catalog import environment_enabled, expand_tools
from harness.backend import Backend
from harness.config import Settings

log = logging.getLogger("harness.profiles")

_SAFE = re.compile(r"[^A-Za-z0-9._-]")
HASH_MARKER = ".rt_profile_hash"
# Muda quando o config.yaml gerado muda de forma (forca regerar perfis existentes): cfg2 = api_max_retries/ambiente rm.
PROFILE_CONFIG_REV = "cfg2"
MAX_USER_SKILLS = 50  # ativas por usuario (spec 3.1)
MAX_USER_SKILL_BYTES = 32 * 1024
MAX_USER_SKILLS_TOTAL = 256 * 1024  # inline por run


def _seg(s: str | None) -> str:
    return _SAFE.sub("_", s or "shared")


def profile_name(tenant_id: str, template_id: str, user_id: str | None,
                 contact: str | None = None) -> str:
    if contact is not None:
        who = "x_" + hashlib.sha256(contact.encode()).hexdigest()[:20]
    else:
        who = _seg(user_id)
    return f"{_seg(tenant_id)}__{_seg(template_id)}__{who}"


def short_profile_name(tenant_id: str, template_id: str, user_id: str | None,
                       contact: str | None = None) -> str:
    """Id curto do Hermes (<=64, [a-z0-9_-]): o mesmo que o gerador de perfis usa em disco."""
    from profiles.profile_config import profile_id
    return profile_id(tenant_id, template_id, user_id, contact)


_SYS_FP: dict[str, str] = {}


def system_skills_fingerprint(root: str) -> str:
    """Digest do conteudo das skills de sistema (muda a cada skill nova/editada, forcando regerar perfis)."""
    if root not in _SYS_FP:
        h = hashlib.sha256()
        base = Path(root)
        if base.is_dir():
            for f in sorted(base.rglob("SKILL.md")):
                h.update(f.relative_to(base).as_posix().encode())
                h.update(f.read_bytes())
        _SYS_FP[root] = h.hexdigest()[:12]
    return _SYS_FP[root]


def template_hash(*, version_id: str, package_sha: str, system_rev: str, manifest: dict,
                  model: dict) -> str:
    raw = json.dumps({"v": version_id, "p": package_sha, "s": system_rev, "m": manifest,
                      "model": model}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class ProfileGenerator(Protocol):
    def generate(self, **kwargs) -> dict: ...


class StubProfileGenerator:
    """Config minima (nao e a configuracao real do WP-B; so deixa o fluxo funcional)."""

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, *, profile_name: str, profiles_root: str, template_hash: str,
                 manifest: dict, model: dict, skills_dirs: dict, llm: dict, **_: object) -> dict:
        self.calls += 1
        home = Path(profiles_root) / profile_name
        for sub in ("pending/skills", "pending/memory", "memories", "sessions"):
            (home / sub).mkdir(parents=True, exist_ok=True)
        ext = [d for d in (skills_dirs.get("system"), skills_dirs.get("tenant"),
                           skills_dirs.get("user")) if d]
        cfg = (
            "# gerado pelo kernel-harness (stub; WP-B substitui por profiles.generate)\n"
            f"model: {{default: {model.get('alias', 'agente-padrao')}}}\n"
            f"skills:\n  external_dirs: {json.dumps(ext)}\n  write_approval: true\n"
            "memory: {write_approval: true}\n"
        )
        (home / "config.yaml").write_text(cfg, encoding="utf-8")
        return {"home": str(home), "config_hash": hashlib.sha256(cfg.encode()).hexdigest()}


class RealProfileGenerator:
    """Adaptador do gerador do WP-B (profiles.profile_config.write_profile)."""

    def __init__(self, settings: Settings) -> None:
        self.s = settings

    def generate(self, *, tenant_id: str, template_id: str, user_id: str | None, manifest: dict,
                 model: dict, template_hash: str = "", contact: str | None = None,
                 **_: object) -> dict:
        from profiles import profile_config as pc
        m = dict(manifest or {})
        if model and model.get("alias") and not m.get("model_alias"):
            m["model_alias"] = model["alias"]
        # G-9: o motor manda `model.gateway` (base_url unica + chave restrita ao combo); sem isso, env legado
        gw = (model or {}).get("gateway") or {}
        llm_base_url = str(gw.get("base_url") or "").rstrip("/") or f"{self.s.litellm_url}/v1"
        llm_key = str(gw.get("api_key") or "") or self.s.llm_worker_key
        paths = pc.Paths(system_skills=self.s.worker_system_skills_dir,
                         tenant_skills_root=self.s.tenant_skills_root,
                         users_root=self.s.users_root, plugin_src=self.s.plugin_src,
                         llm_base_url=llm_base_url)
        r = pc.write_profile(self.s.profiles_root, tenant_id, template_id, user_id, m,
                             self.s.hermes_master_secret, paths, template_version="current",
                             llm_api_key=llm_key, contact=contact)
        return {"home": r["path"], "config_hash": template_hash}


def load_generator(settings: Settings | None = None) -> ProfileGenerator:
    if settings is not None and settings.hermes_master_secret:
        return RealProfileGenerator(settings)
    return StubProfileGenerator()


def safe_relpath(path: str) -> str | None:
    p = path.replace("\\", "/")
    if not p or p.startswith("/") or re.match(r"^[A-Za-z]:", p):
        return None
    parts = p.split("/")
    if any(x in ("", ".", "..") or x.startswith(".") for x in parts):
        return None
    return p


def _atomic_write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    if isinstance(content, bytes):
        tmp.write_bytes(content)
    else:
        tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


ASSETS_DIR = "arquivos"  # pasta reservada do pacote: arquivos de qualquer tipo (spec 10, H-2)
MAX_ASSET_BYTES = 25 * 1024 * 1024  # o backend limita a 10 MB; aqui e so um cinto de seguranca
MAX_ASSETS_TOTAL = 100 * 1024 * 1024


def decode_package_file(f: dict) -> bytes:
    """Bytes do arquivo do pacote. `encoding: "base64"` = binario; senao, texto UTF-8.

    Se vier `sha256`, ele e conferido contra os bytes decodificados (pacote adulterado ou
    truncado no transporte nao chega a disco).
    """
    content = f.get("content")
    if not isinstance(content, str):
        raise ValueError(f"arquivo sem conteudo no pacote: {f.get('path')!r}")
    if f.get("encoding") == "base64":
        try:
            data = base64.b64decode(content, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"base64 invalido no pacote: {f.get('path')!r}") from exc
        if len(data) > MAX_ASSET_BYTES:
            raise ValueError(f"arquivo grande demais no pacote: {f.get('path')!r}")
        expected = f.get("sha256")
        if expected and hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"sha256 do arquivo nao confere: {f.get('path')!r}")
        return data
    return content.encode("utf-8")


def list_package_assets(tenant_dir: Path | None, limit: int = 60) -> list[tuple[str, int]]:
    """[(caminho relativo ao pacote, tamanho)] dos arquivos de `arquivos/` ja em disco."""
    if tenant_dir is None:
        return []
    root = tenant_dir / ASSETS_DIR
    if not root.is_dir():
        return []
    out = []
    for p in sorted(x for x in root.rglob("*") if x.is_file() and not x.is_symlink()):
        out.append((p.relative_to(tenant_dir).as_posix(), p.stat().st_size))
        if len(out) >= limit:
            break
    return out


class ProfileManager:
    def __init__(self, settings: Settings, backend: Backend,
                 generator: ProfileGenerator | None = None):
        self.s = settings
        self.backend = backend
        self.gen = generator or load_generator(settings)

    # ---- pacote do tenant (camada 2) -------------------------------------
    async def materialize_tenant_package(self, template_id: str, version_id: str,
                                         package: dict | None) -> Path | None:
        base = Path(self.s.tenant_skills_root) / _seg(template_id)
        target = base / _seg(version_id)
        if package is None:
            return target if target.exists() else None
        marker = target / ".sha256"
        sha = package.get("sha256", "")
        if marker.exists() and marker.read_text(encoding="utf-8") == sha:
            self._link_current(base, target)
            return target
        files = package.get("files")
        if files is None and package.get("url"):
            files = (await self.backend.fetch_package(package["url"])).get("files")
        if files is None:
            raise ValueError("pacote do template sem arquivos nem url")
        tmp = base / f".tmp-{uuid.uuid4().hex[:8]}"
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            total = 0
            for f in files:
                rel = safe_relpath(f["path"])
                if rel is None:
                    raise ValueError(f"caminho invalido no pacote: {f['path']!r}")
                data = decode_package_file(f)
                total += len(data)
                if total > MAX_ASSETS_TOTAL + 4 * 1024 * 1024:
                    raise ValueError("pacote do template grande demais")
                _atomic_write(tmp / rel, data)
            (tmp / ".sha256").write_text(sha, encoding="utf-8")
            if target.exists():
                shutil.rmtree(target)
            os.replace(tmp, target)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self._link_current(base, target)
        return target

    @staticmethod
    def _link_current(base: Path, target: Path) -> None:
        cur = base / "current"
        tmp = base / f".current-{uuid.uuid4().hex[:6]}"
        try:
            os.symlink(target, tmp, target_is_directory=True)
            os.replace(tmp, cur)
        except OSError:  # Windows sem privilegio: copia atomica
            shutil.rmtree(tmp, ignore_errors=True)
            if cur.is_symlink() or cur.is_file():
                cur.unlink()
            elif cur.exists():
                shutil.rmtree(cur)
            shutil.copytree(target, cur)

    # ---- skills do usuario (camada 3) ------------------------------------
    async def materialize_user_skills(self, tenant_id: str, user_id: str,
                                      inline: list[dict] | None) -> Path:
        """Escreve os .md aprovados em users_root/<t>/<u>/skills (leitura nativa do Hermes)."""
        root = Path(self.s.users_root) / _seg(tenant_id) / _seg(user_id) / "skills"
        root.mkdir(parents=True, exist_ok=True)
        try:
            skills = await self.backend.get_user_skills(tenant_id, user_id)
        except Exception as exc:  # noqa: BLE001 - sem backend usamos o que veio inline
            log.warning("skills do usuario indisponiveis (%s); usando inline", type(exc).__name__)
            skills = [s for s in (inline or []) if s.get("content")]
        wanted: dict[str, str] = {}
        total = 0
        for s in skills[: MAX_USER_SKILLS * 2]:
            name = s.get("name", "")
            if not re.fullmatch(r"u-[a-z0-9][a-z0-9._-]{0,61}", name) or not s.get("content"):
                continue
            if s.get("status", "active") != "active":
                continue
            size = len(s["content"].encode("utf-8"))
            if (len(wanted) >= MAX_USER_SKILLS or size > MAX_USER_SKILL_BYTES
                    or total + size > MAX_USER_SKILLS_TOTAL):
                log.info("skill de usuario ignorada por limite: %s", name)
                continue
            total += size
            wanted[name] = s["content"]
        for name, content in wanted.items():
            p = root / name / "SKILL.md"
            if not p.exists() or p.read_text(encoding="utf-8") != content:
                _atomic_write(p, content)
        for d in root.iterdir():
            if d.is_dir() and d.name not in wanted:
                shutil.rmtree(d, ignore_errors=True)
        return root

    # ---- perfil -----------------------------------------------------------
    async def ensure(self, *, tenant_id: str, template_id: str, user_id: str | None,
                     version_id: str, manifest: dict, model: dict, skills: dict,
                     contact: str | None = None) -> tuple[str, Path | None]:
        """`contact` (K-9): id da conversa de um cliente EXTERNO. Com ele o perfil e proprio do contato
        (nunca o 'shared' do template), sem memoria/session_search e sem skills de usuario."""
        if contact is not None:
            user_id = None
        name = (short_profile_name(tenant_id, template_id, user_id, contact)
                if self.s.hermes_master_secret else profile_name(tenant_id, template_id, user_id, contact))
        pkg = skills.get("tenant_package")
        tenant_dir = await self.materialize_tenant_package(template_id, version_id, pkg)
        user_dir = None
        if user_id:
            user_dir = await self.materialize_user_skills(tenant_id, user_id,
                                                          skills.get("user_skills"))
        key_fp = hashlib.sha256(self.s.llm_worker_key.encode()).hexdigest()[:12]
        h = template_hash(version_id=version_id, package_sha=(pkg or {}).get("sha256", ""),
                          system_rev=skills.get("system_rev", "") + "|" + system_skills_fingerprint(self.s.system_skills_dir) + "|" + key_fp + "|" + PROFILE_CONFIG_REV
                          + ("|ext" if contact is not None else ""),
                          manifest=manifest, model=model)
        home = Path(self.s.profiles_root) / name
        marker = home / HASH_MARKER
        if not marker.exists() or marker.read_text(encoding="utf-8") != h:
            log.info("gerando perfil %s", name)
            self.gen.generate(
                profile_name=name, profiles_root=self.s.profiles_root, tenant_id=tenant_id,
                template_id=template_id, user_id=user_id, template_version_id=version_id,
                template_hash=h, contact=contact,
                manifest={**manifest, "tools": expand_tools(manifest.get("tools"), environment_enabled(manifest))},
                model=model,
                skills_dirs={"system": self.s.system_skills_dir,
                             "tenant": str(Path(self.s.tenant_skills_root) / _seg(template_id)
                                           / "current") if tenant_dir else None,
                             "user": str(user_dir) if user_dir else None},
                llm={"base_url": f"{self.s.litellm_url}/v1", "key_cmd": None})
            for sub in ("pending/skills", "pending/memory"):
                (home / sub).mkdir(parents=True, exist_ok=True)
            _atomic_write(marker, h)
        return name, tenant_dir

    @staticmethod
    def read_agent_md(tenant_dir: Path | None, package: dict | None) -> str:
        if tenant_dir is not None and (tenant_dir / "AGENTE.md").exists():
            return (tenant_dir / "AGENTE.md").read_text(encoding="utf-8")
        for f in (package or {}).get("files") or []:
            if f.get("path") == "AGENTE.md":
                return f["content"]
        return ""

    def package_dir(self, template_id: str, version_id: str) -> Path:
        return Path(self.s.tenant_skills_root) / _seg(template_id) / _seg(version_id)
