"""Anexos da conversa como arquivos: o agente os usa em codigo (executor) ou no ambiente."""
from __future__ import annotations

import posixpath

from broker.errors import ToolError
from harness.runctx import RunContext

MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024


def find(ctx: RunContext, name: str) -> dict:
    base = posixpath.basename(str(name or ""))
    for a in ctx.attachments:
        if a["name"] == base and a.get("ref"):
            return a
    names = ", ".join(a["name"] for a in ctx.attachments) or "nenhum"
    raise ToolError("invalid_args", f"Anexo '{base}' nao encontrado nesta conversa (anexos: {names}).")


async def read(svc, ctx: RunContext, name: str) -> bytes:
    att = find(ctx, name)
    try:
        data = await svc.backend.fetch_file(ctx.run_id, ctx.run_token, att["ref"])
    except Exception as exc:  # noqa: BLE001
        raise ToolError("unavailable", "Nao foi possivel abrir o anexo agora.") from exc
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise ToolError("limit", "O anexo e grande demais para esta ferramenta (max. 50 MB).")
    return data
