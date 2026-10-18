"""Estado em memoria de uma run: credenciais, manifesto, contadores, aprovacoes.

REGRA (D-57): credenciais so vivem aqui. Nunca em Redis, disco ou log. `forget()` roda no
`finally` do dispatcher (inclusive erro/cancelamento) e zera tudo.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from harness.security import Redactor

SENSITIVE_MARK = "[REDACTED]"


def session_id_for(tenant_id: str, user_id: str | None, chat_id: str) -> str:
    return f"{tenant_id}:{user_id or 'shared'}:{chat_id}"


def wipe_obj(node: Any) -> None:
    if isinstance(node, dict):
        for k in list(node):
            wipe_obj(node[k])
            node[k] = None
        node.clear()
    elif isinstance(node, list):
        for v in node:
            wipe_obj(v)
        node.clear()


@dataclass
class ApprovalWait:
    approval_id: str
    future: asyncio.Future
    kind: str
    summary: str


@dataclass
class RunContext:
    run_id: str
    tenant_id: str
    user_id: str | None
    chat_id: str
    template_id: str
    version_id: str
    channel: str
    actor: dict
    session_id: str
    profile: str = ""
    worker_id: str = ""
    manifest: dict = field(default_factory=dict)
    credentials: dict = field(default_factory=dict)
    run_token: str = ""
    redactor: Redactor = field(default_factory=lambda: Redactor([]))
    # contadores / caches por turno
    tool_calls: int = 0
    limit_emitted: bool = False
    read_cache: dict = field(default_factory=dict)
    emitted_artifacts: set = field(default_factory=set)
    pix_results: dict = field(default_factory=dict)
    # aprovacoes
    approvals: dict[str, ApprovalWait] = field(default_factory=dict)
    paused_total: float = 0.0
    _pause_started: float | None = None
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    # saida para o stream do produto (injetado pelo dispatcher)
    emit: Any = None
    forgotten: bool = False
    started_at: float = field(default_factory=time.monotonic)

    # ---- manifesto
    @property
    def tools_allowed(self) -> set[str]:
        from broker.catalog import expand_tools

        return set(expand_tools(self.manifest.get("tools")))

    @property
    def limits(self) -> dict:
        return self.manifest.get("limits") or {}

    def granted(self, kind: str | None) -> bool:
        """Grants do template (R-19): se o manifesto traz a lista, so vale o que esta nela."""
        if kind is None or "credential_grants" not in self.manifest:
            return True
        return any(g.get("kind") == kind for g in self.manifest.get("credential_grants") or [])

    def cred_list(self, key: str) -> list[dict]:
        return list((self.credentials or {}).get(key) or [])

    # ---- pausa (espera de aprovacao nao conta para timeout)
    def pause(self) -> None:
        if self._pause_started is None:
            self._pause_started = time.monotonic()

    def resume(self) -> None:
        if self._pause_started is not None:
            self.paused_total += time.monotonic() - self._pause_started
            self._pause_started = None

    @property
    def is_paused(self) -> bool:
        return self._pause_started is not None

    def effective_now(self) -> float:
        now = time.monotonic()
        ongoing = (now - self._pause_started) if self._pause_started is not None else 0.0
        return now - self.paused_total - ongoing

    # ---- esquecer
    def forget(self) -> None:
        if self.forgotten:
            return
        self.forgotten = True
        wipe_obj(self.credentials)
        self.credentials = {}
        self.run_token = ""
        self.redactor.clear()
        self.read_cache.clear()
        self.pix_results.clear()
        for w in self.approvals.values():
            if not w.future.done():
                w.future.set_result("denied")  # run encerrada: libera a ferramenta em espera
        self.approvals.clear()
        self.emit = None


class RunContexts:
    """Registro de runs ativas: o broker so autoriza com uma run aqui."""

    def __init__(self) -> None:
        self._by_session: dict[str, RunContext] = {}
        self._by_run: dict[str, RunContext] = {}

    def register(self, ctx: RunContext) -> None:
        self._by_session[ctx.session_id] = ctx
        self._by_run[ctx.run_id] = ctx

    def by_session(self, session_id: str) -> RunContext | None:
        return self._by_session.get(session_id)

    def by_run(self, run_id: str) -> RunContext | None:
        return self._by_run.get(run_id)

    def forget(self, run_id: str) -> None:
        ctx = self._by_run.pop(run_id, None)
        if ctx is None:
            return
        if self._by_session.get(ctx.session_id) is ctx:
            self._by_session.pop(ctx.session_id, None)
        ctx.forget()

    def active(self) -> int:
        return len(self._by_run)

    def all(self) -> list[RunContext]:
        return list(self._by_run.values())
