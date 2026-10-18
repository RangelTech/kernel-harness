"""Registro de artefatos (reaproveita register-init/complete do backend)."""
from __future__ import annotations

import json
import uuid

import httpx

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
    init = await svc.backend.artifact_register_init(ctx.run_id, ctx.run_token, {
        "tenant_id": ctx.tenant_id, "user_id": ctx.user_id, "chat_id": ctx.chat_id,
        "kind": kind, "extension": extension.lstrip("."), "content_type": content_type,
        "size_bytes": len(data)})
    artifact_id = str(init.get("artifact_id") or uuid.uuid4())
    upload = init.get("upload_url")
    if upload:
        try:
            r = await svc.http.put(upload, content=data, headers={"Content-Type": content_type},
                                   timeout=60.0)
        except httpx.HTTPError as exc:
            raise ToolError("unavailable", "Não foi possível salvar o arquivo agora.") from exc
        if r.status_code >= 300:
            raise ToolError("unavailable", "Não foi possível salvar o arquivo agora.")
    await svc.backend.artifact_register_complete(ctx.run_id, ctx.run_token, {
        "artifact_id": artifact_id, "tenant_id": ctx.tenant_id, "user_id": ctx.user_id,
        "chat_id": ctx.chat_id, "agent_name": "agente", "title": title, "kind": kind,
        "schema_json": schema, "preview_json": preview, "row_count": row_count})
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
