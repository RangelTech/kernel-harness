"""Registro de artefatos: entrega os bytes ao backend (que guarda e registra)."""
from __future__ import annotations

import base64
import json
import uuid

from broker.errors import ToolError
from harness.runctx import RunContext

MAX_ARTIFACT_BYTES = 25 * 1024 * 1024
KINDS = {"file", "image", "dataset", "document", "chart", "html"}


async def put_artifact(svc, ctx: RunContext, *, kind: str, title: str, data: bytes,
                       extension: str, content_type: str, schema: object = None,
                       preview: object = None, row_count: int | None = None) -> dict:
    if kind not in KINDS:
        kind = "file"
    if kind == "document":
        kind = "file"
    if len(data) > MAX_ARTIFACT_BYTES:
        raise ToolError("limit", "O arquivo é grande demais para ser registrado.")
    try:
        res = await svc.backend.artifact_put(ctx.run_id, ctx.run_token, {
            "kind": kind, "title": title, "extension": extension.lstrip("."),
            "content_type": content_type, "content_base64": base64.b64encode(data).decode(),
            "columns": schema, "preview_json": preview, "row_count": row_count,
            "agent_name": "agente"})
    except Exception as exc:  # noqa: BLE001
        raise ToolError("unavailable", "Não foi possível salvar o arquivo agora.") from exc
    artifact_id = str(res.get("artifact_id") or uuid.uuid4())
    desc = {"artifact_id": artifact_id, "kind": kind, "title": title}
    if schema is not None:
        desc["schema"] = schema
    if preview is not None:
        desc["preview"] = preview
    if row_count is not None:
        desc["row_count"] = row_count
    if artifact_id not in ctx.emitted_artifacts:
        ctx.emitted_artifacts.add(artifact_id)
        if ctx.emit:
            await ctx.emit("artifact", {"artifact_id": artifact_id, "kind": kind, "title": title})
    return desc


def json_bytes(obj: object) -> bytes:
    return json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
