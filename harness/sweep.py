"""Promotor: varre `pending/skills` e `pending/memory` do perfil apos cada run (spec 3.5).

O perfil ja e do usuario, entao a atribuicao do aprendizado e certa. Nada e ativado aqui:
tudo vai ao backend como `pending_review`; a camada 3 so ganha o .md quando o usuario aprova.
Formato dos JSON: `{"id", "kind", "payload": {...kwargs do replay do Hermes...}}` (formato
interno do Hermes: coberto pela bateria de contrato H6; ver anexos/04/hermes-agent-fonte.md).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from harness.backend import Backend
from harness.config import Settings
from harness.runctx import RunContext
from harness.skillcheck import ensure_frontmatter, split_frontmatter, validate_user_skill

log = logging.getLogger("harness.sweep")


@dataclass
class SweepResult:
    skills: list[dict]
    memories: int
    rejected: int


def reserved_skill_names(settings: Settings, template_id: str) -> set[str]:
    names: set[str] = set()
    for root in (Path(settings.system_skills_dir),
                 Path(settings.tenant_skills_root) / template_id / "current" / "skills"):
        if root.is_dir():
            for p in root.rglob("SKILL.md"):
                names.add(p.parent.name)
    return names


async def _promote_skill(backend: Backend, ctx: RunContext, op: dict, summary: str,
                         reserved: set[str], secrets: list[str], res: SweepResult) -> None:
    name, content, err = validate_user_skill(
        op["name"], ensure_frontmatter(op["name"], op["content"], summary),
        reserved_names=reserved, secret_values=secrets)
    if err:
        log.warning("skill proposta recusada run=%s: %s", ctx.run_id, err)
        res.rejected += 1
        return
    fm, _b, _e = split_frontmatter(content)
    out = await backend.post_skill_proposal(ctx.run_id, ctx.run_token, {
        "tenant_id": ctx.tenant_id, "user_id": ctx.user_id,
        "template_id": ctx.template_id, "chat_id": ctx.chat_id,
        "name": name, "description": (fm or {}).get("description", ""),
        "content": content, "source": "agent", "status": "pending_review"})
    res.skills.append({"user_skill_id": str(out.get("id") or out.get("user_skill_id") or name),
                       "name": name, "status": out.get("status", "pending_review")})


async def sweep_pending(settings: Settings, backend: Backend, ctx: RunContext,
                        profile_home: Path) -> SweepResult:
    res = SweepResult([], 0, 0)
    sk_dir, mem_dir = profile_home / "pending" / "skills", profile_home / "pending" / "memory"
    has_user = bool(ctx.user_id)
    reserved = reserved_skill_names(settings, ctx.template_id)
    secrets = list(ctx.redactor.values)

    if sk_dir.is_dir():
        for f in sorted(sk_dir.glob("*.json")):
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
                p = doc.get("payload") or {}
                # o Hermes atual enfileira `batch` com operations[]; versoes antigas, uma operacao plana
                ops = [o for o in (p.get("operations") or []) if isinstance(o, dict)]                     if str(p.get("action") or "").lower() == "batch" else [p]
                for op in ops:
                    action = (op.get("action") or "create").lower()
                    if not has_user:
                        res.rejected += 1  # contato externo: sem camada 3 (privacidade)
                    elif action != "create" or not op.get("name") or not op.get("content"):
                        log.warning("pending skill ignorado run=%s (acao %s, chaves %s)",
                                    ctx.run_id, action, sorted(op))
                        res.rejected += 1
                    else:
                        await _promote_skill(backend, ctx, op, str(doc.get("summary") or ""),
                                             reserved, secrets, res)
            except Exception:  # noqa: BLE001
                log.exception("falha ao promover %s", f.name)
                res.rejected += 1
                continue  # mantem o arquivo para nova tentativa na proxima run
            f.unlink(missing_ok=True)

    if mem_dir.is_dir():
        for f in sorted(mem_dir.glob("*.json")):
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
                if has_user:
                    text = str((doc.get("payload") or {}).get("content") or "")[:2000]
                    if text and not any(v in text for v in secrets):
                        await backend.post_memory_proposal(ctx.run_id, ctx.run_token, {
                            "tenant_id": ctx.tenant_id, "user_id": ctx.user_id,
                            "chat_id": ctx.chat_id, "text": text, "status": "pending_review"})
                        res.memories += 1
            except Exception:  # noqa: BLE001
                log.exception("falha ao promover memoria %s", f.name)
                continue
            f.unlink(missing_ok=True)
    return res
