"""Publicacao de eventos do produto (contrato 4.2) no stream `events:<run_id>`."""
from __future__ import annotations

import logging

from harness.runctx import RunContext
from harness.store import Store

log = logging.getLogger("harness.events")

TERMINAL_EVENTS = {"done", "error"}

ERROR_MESSAGES = {
    "timeout": "A solicitação demorou mais que o esperado e foi encerrada. Tente novamente.",
    "empty_answer": "Não consegui montar uma resposta desta vez. Tente reformular a pergunta.",
    "interrupted": "A execução foi interrompida antes de terminar. Envie a mensagem novamente.",
    "cancelled": "Execução cancelada.",
    "failed": "Não foi possível concluir a solicitação. Tente novamente em instantes.",
    "partial_answer": "A resposta ficou incompleta. Envie a mensagem novamente.",
    "rate_limited": "O modelo está ocupado ou o limite de uso foi atingido. Tente novamente em instantes.",
    "model_rate_limited": "O modelo está ocupado ou o limite de uso foi atingido. Tente novamente em instantes.",
    "model_unavailable": "O modelo de IA está indisponível no momento. Tente novamente em instantes.",
    "queue_expired": "Muitas solicitações no momento. Tente novamente em instantes.",
    "tool_limit": "A execução atingiu o limite de ações permitido e foi encerrada.",
    "tenant_suspended": "Este recurso está indisponível para sua empresa no momento.",
    "kernel_restarted": "O serviço foi reiniciado antes de processar sua mensagem. Envie novamente.",
    "worker_unavailable": "O agente está temporariamente indisponível. Tente novamente em instantes.",
    "busy": "O agente está ocupado no momento. Tente novamente em instantes.",
    "invalid_request": "Solicitação inválida.",
}


class EventBus:
    def __init__(self, store: Store):
        self.store = store

    async def publish(self, run_id: str, etype: str, data: dict,
                      ctx: RunContext | None = None) -> str | None:
        if ctx is not None and ctx.redactor.values:
            data = ctx.redactor.scrub_obj(data)
        if etype in TERMINAL_EVENTS:
            # um unico terminal por run (cancel x completed corrida)
            if not await self.store.r.hsetnx(f"run:{run_id}", "terminal_event", etype):
                log.info("terminal duplicado ignorado run=%s type=%s", run_id, etype)
                return None
        return await self.store.add_event(run_id, etype, data)

    async def error(self, run_id: str, code: str, ctx: RunContext | None = None,
                    detail: str | None = None) -> str | None:
        return await self.publish(
            run_id, "error", {"detail": detail or ERROR_MESSAGES.get(code, "Erro."), "code": code},
            ctx)
