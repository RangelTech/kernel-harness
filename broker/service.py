"""Broker: unico ponto onde credenciais da run sao usadas (spec 6.2).

Autorizacao real = casar `session_id` com uma run ATIVA que o kernel abriu E o token do worker
que a esta atendendo. O token do plugin sozinho nao autoriza nada.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from broker import data, http_tool, misc, pix
from broker import email_tool as email_mod
from broker import env as env_mod
from broker import executor as executor_mod
from broker import schedule as schedule_mod
from broker.errors import ToolError
from harness.approvals import ApprovalService
from harness.backend import Backend
from harness.config import Settings
from harness.runctx import RunContext, RunContexts
from harness.workers import WorkerRegistry

log = logging.getLogger("harness.broker")
audit_log = logging.getLogger("harness.audit")

Handler = Callable[..., Awaitable[object]]


@dataclass(frozen=True)
class ToolSpec:
    handler: Handler
    cred_kind: str | None = None


TOOLS: dict[str, ToolSpec] = {
    "rt_echo": ToolSpec(misc.echo),
    "rt_data_describe": ToolSpec(data.describe, "datasource"),
    "rt_sql_read": ToolSpec(data.sql_read, "datasource"),
    "rt_mongo_find": ToolSpec(data.mongo_find, "datasource"),
    "rt_sql_write": ToolSpec(data.sql_write, "datasource"),
    "rt_sql_transaction": ToolSpec(data.sql_transaction, "datasource"),
    "rt_pix_create": ToolSpec(pix.create, "mercado_pago"),
    "rt_payment_status": ToolSpec(pix.payment_status, "mercado_pago"),
    "rt_mp_read": ToolSpec(pix.mp_read, "mercado_pago"),
    "rt_email_list": ToolSpec(email_mod.list_inbox, "email"),
    "rt_email_send": ToolSpec(email_mod.send, "email"),
    "rt_http": ToolSpec(http_tool.call, "secrets"),
    "rt_search_docs": ToolSpec(misc.search_docs),
    "rt_memory_recall": ToolSpec(misc.memory_recall),
    "rt_artifact_put": ToolSpec(misc.artifact_put),
    "rt_executor_run": ToolSpec(executor_mod.run),
    "rt_env_status": ToolSpec(env_mod.status),
    "rt_env_start": ToolSpec(env_mod.start),
    "rt_env_exec": ToolSpec(env_mod.exec_),
    "rt_env_stop": ToolSpec(env_mod.stop),
    "rt_env_backup": ToolSpec(env_mod.backup),
    "rt_env_expose": ToolSpec(env_mod.expose),
    "rt_env_unexpose": ToolSpec(env_mod.unexpose),
    "rt_env_domain_add": ToolSpec(env_mod.domain_add),
    "rt_env_domain_verify": ToolSpec(env_mod.domain_verify),
    "rt_schedule_create": ToolSpec(schedule_mod.create),
    "rt_schedule_list": ToolSpec(schedule_mod.list_),
    "rt_schedule_pause": ToolSpec(schedule_mod.pause),
    "rt_schedule_delete": ToolSpec(schedule_mod.delete),
}


class BrokerService:
    def __init__(self, settings: Settings, contexts: RunContexts, workers: WorkerRegistry,
                 approvals: ApprovalService, backend: Backend, http: httpx.AsyncClient,
                 email: email_mod.EmailTransport | None = None, resolver=None):
        self.s = settings
        self.contexts = contexts
        self.workers = workers
        self.approvals = approvals
        self.backend = backend
        self.http = http
        self.email = email or email_mod.StdlibEmailTransport()
        self.resolver = resolver or http_tool.default_resolver
        self.stats = {"calls": 0, "denied": 0, "errors": 0}

    # ---------------------------------------------------------------- confirmacao
    async def confirm(self, ctx: RunContext, *, kind: str, risk: str, summary: str,
                      details: dict | None = None) -> None:
        res = await self.approvals.request(ctx, kind=kind, summary=ctx.redactor.scrub(summary),
                                           risk=risk, details=details)
        if res == "approved":
            return
        if res == "expired":
            raise ToolError("approval_expired", "A confirmação expirou. Nada foi executado.")
        if res == "external_denied":
            raise ToolError("approval_denied", "Este canal não permite executar esta ação.")
        raise ToolError("approval_denied", "O usuário não autorizou a ação. Nada foi executado.")

    # ------------------------------------------------------------------- limites
    def _call_cap(self, ctx: RunContext) -> int:
        cap = int(ctx.limits.get("max_tool_calls_per_turn") or self.s.max_tool_calls_per_turn)
        return max(5, min(cap, 200))

    def _truncate(self, payload: dict) -> dict:
        raw = json.dumps(payload, ensure_ascii=False, default=str)
        limit = self.s.tool_output_limit
        if len(raw) <= limit:
            return payload
        return {"ok": payload.get("ok", True), "truncated": True,
                "note": ("Resultado cortado: NÃO some nem conclua com base nele, está "
                         "incompleto. Peça menos dados (filtre/agregue) ou use o artefato."),
                "preview": raw[: limit - 400]}

    # ---------------------------------------------------------------------- call
    async def call(self, bearer: str, body: dict) -> tuple[int, dict]:
        """-> (http_status, corpo). 401/403 para falhas de autorizacao; 200 para o resto."""
        worker_id = self.workers.broker_identity(bearer) if bearer else None
        if worker_id is None:
            self.stats["denied"] += 1
            return 401, {"detail": "Não autorizado.", "code": "unauthorized"}
        session_id = str(body.get("session_id") or "")
        tool = str(body.get("tool") or "")
        args = body.get("args")
        if not session_id or not tool or not isinstance(args, dict):
            return 422, {"detail": "Chamada inválida.", "code": "invalid_request"}
        ctx = self.contexts.by_session(session_id)
        if ctx is None or ctx.forgotten:
            self.stats["denied"] += 1
            return 401, {"detail": "Nenhuma execução ativa para esta sessão.",
                         "code": "no_active_run"}
        if worker_id != "*" and ctx.worker_id != worker_id:
            self.stats["denied"] += 1
            log.warning("broker: worker %s tentou usar sessao de outro worker (run=%s)",
                        worker_id, ctx.run_id)
            return 403, {"detail": "Sessão de outro contexto.", "code": "foreign_session"}
        if tool not in TOOLS or tool not in ctx.tools_allowed:
            self.stats["denied"] += 1
            self._audit(ctx, tool, None, False, "tool_not_allowed")
            return 403, {"detail": "Esta ação não está disponível para este agente.",
                         "code": "tool_not_allowed"}

        spec = TOOLS[tool]
        self.stats["calls"] += 1
        ctx.tool_calls += 1
        t0 = time.monotonic()
        try:
            if ctx.tool_calls > self._call_cap(ctx):
                raise ToolError("limit", "Limite de ações do turno atingido. Responda agora com o "
                                         "que já tem.")
            result = await spec.handler(self, ctx, args)
            payload = {"ok": True, "result": result}
            code = "ok"
        except ToolError as exc:
            payload = {"ok": False, "error": {"code": exc.code, "message": exc.message}}
            code = exc.code
        except Exception as exc:  # noqa: BLE001
            self.stats["errors"] += 1
            log.exception("ferramenta %s falhou run=%s", tool, ctx.run_id)
            payload = {"ok": False, "error": {"code": "tool_error",
                                              "message": f"Falha interna ({type(exc).__name__})."}}
            code = "tool_error"
        if ctx.forgotten:  # run terminou (cancel/timeout) enquanto a ferramenta rodava
            return 200, {"ok": False, "error": {"code": "unavailable",
                                                "message": "A execução foi encerrada."}}
        payload = ctx.redactor.scrub_obj(payload)
        payload = self._truncate(payload)
        self._audit(ctx, tool, spec.cred_kind, code == "ok", code, (time.monotonic() - t0) * 1000)
        return 200, payload

    def _audit(self, ctx: RunContext, tool: str, cred_kind: str | None, ok: bool, code: str,
               ms: float = 0.0) -> None:
        """Auditoria por ferramenta SEM valores (spec 8.1 `tool_audit`)."""
        rec = {"run_id": ctx.run_id, "tenant_id": ctx.tenant_id, "user_id": ctx.user_id,
               "chat_id": ctx.chat_id, "template_id": ctx.template_id, "tool": tool,
               "credential_kind": cred_kind, "ok": ok, "code": code, "ms": round(ms, 1)}
        audit_log.info(json.dumps(rec, ensure_ascii=False))
