"""rt_schedule_*: agendamentos recorrentes da PLATAFORMA (spec 04 10).

O agendamento vive no backend (tabela de agendamentos); no horario a plataforma abre uma conversa
e acorda o agente com a instrucao salva. Criar exige confirmacao autenticada (envio em massa etc.).
"""
from __future__ import annotations

import re

from broker.errors import ToolError
from harness.runctx import RunContext

CRON_RE = re.compile(r"^\S+( \S+){4}$")


async def _op(svc, ctx: RunContext, op: str, payload: dict) -> dict:
    try:
        return await svc.backend.schedule_op(ctx.run_id, ctx.run_token, {"op": op, **payload})
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ToolError("unavailable", "Os agendamentos estão indisponíveis agora.") from exc


async def create(svc, ctx: RunContext, args: dict):
    name = str(args.get("name") or "").strip()
    cron = " ".join(str(args.get("cron") or "").split())
    instruction = str(args.get("instruction") or "").strip()
    tz = str(args.get("timezone") or "America/Sao_Paulo").strip()
    if not name or len(name) > 80:
        raise ToolError("invalid_args", "Informe um nome curto para a rotina.")
    if not CRON_RE.match(cron):
        raise ToolError("invalid_args", "Horário inválido: use 5 campos (ex.: 0 8 * * * = todo dia às 8h).")
    if len(instruction) < 10 or len(instruction) > 4000:
        raise ToolError("invalid_args", "Descreva o que o agente deve fazer a cada disparo.")
    await svc.confirm(ctx, kind="schedule_create", risk="high",
                      summary=f"Criar a rotina “{name}” ({cron}, {tz}): {instruction[:300]}",
                      details={"name": name, "cron": cron, "timezone": tz})
    return await _op(svc, ctx, "create", {"name": name, "cron": cron, "timezone": tz,
                                          "instruction": instruction})


async def list_(svc, ctx: RunContext, args: dict):
    return await _op(svc, ctx, "list", {})


async def pause(svc, ctx: RunContext, args: dict):
    sid = str(args.get("id") or "").strip()
    if not sid:
        raise ToolError("invalid_args", "Informe o id da rotina.")
    resume = bool(args.get("resume"))
    return await _op(svc, ctx, "resume" if resume else "pause", {"id": sid})


async def delete(svc, ctx: RunContext, args: dict):
    sid = str(args.get("id") or "").strip()
    if not sid:
        raise ToolError("invalid_args", "Informe o id da rotina.")
    await svc.confirm(ctx, kind="schedule_delete", risk="medium",
                      summary="Apagar uma rotina agendada", details={"id": sid})
    return await _op(svc, ctx, "delete", {"id": sid})
