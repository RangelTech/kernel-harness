"""rt_executor_run: roda um script Python num contêiner efemero SEM rede (spec 04 7.3).

Entradas: nome->conteudo texto (ou base64 com `inputs_b64`). Saidas: arquivos que o script grava
em /out ficam registrados como artefatos da conversa (grafico PNG vira imagem no chat).
"""
from __future__ import annotations

import base64

from broker import attachments as attachments_mod
from broker.artifacts import put_artifact
from broker.errors import ToolError
from broker.rm import RMClient
from harness.runctx import RunContext

_KIND = {"png": ("image", "image/png"), "jpg": ("image", "image/jpeg"),
         "jpeg": ("image", "image/jpeg"), "svg": ("image", "image/svg+xml"),
         "gif": ("image", "image/gif"), "csv": ("dataset", "text/csv"),
         "json": ("file", "application/json"), "pdf": ("file", "application/pdf"),
         "xlsx": ("file", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
         "html": ("html", "text/html"), "txt": ("file", "text/plain"),
         "md": ("file", "text/markdown"),
         "mp3": ("file", "audio/mpeg"), "wav": ("file", "audio/wav"), "ogg": ("file", "audio/ogg"),
         "m4a": ("file", "audio/mp4"), "mp4": ("file", "video/mp4"), "mov": ("file", "video/quicktime"),
         "webm": ("file", "video/webm"), "zip": ("file", "application/zip"),
         "docx": ("file", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
         "pptx": ("file", "application/vnd.openxmlformats-officedocument.presentationml.presentation")}
MAX_STD = 8000


def _cut(s: str) -> str:
    return s if len(s) <= MAX_STD else s[:MAX_STD] + "\n…(saída truncada)"


async def run(svc, ctx: RunContext, args: dict):
    script = args.get("script")
    if not isinstance(script, str) or not script.strip():
        raise ToolError("invalid_args", "Informe o script Python.")
    inputs: dict[str, str] = {}
    for name, val in (args.get("inputs") or {}).items():
        inputs[str(name)] = base64.b64encode(str(val).encode("utf-8")).decode()
    for name, val in (args.get("inputs_b64") or {}).items():
        inputs[str(name)] = str(val)
    # sem `attachments` explicito, todos os anexos da conversa entram em /in (o modelo nem sempre pede)
    names = args.get("attachments") or [a["name"] for a in ctx.attachments if a.get("name")]
    for name in names:
        # anexo da conversa vira /in/<nome> (qualquer tipo: planilha, imagem, audio, video, pdf...)
        data = await attachments_mod.read(svc, ctx, str(name))
        inputs[attachments_mod.find(ctx, str(name))["name"]] = base64.b64encode(data).decode()
    try:
        timeout = max(5, min(int(args.get("timeout_s") or 60), 120))
    except (TypeError, ValueError):
        timeout = 60
    res = await RMClient(svc.s, svc.http).call(
        "POST", "/v1/executor/runs", scope="*", timeout=timeout + 30.0,
        body={"script": script, "inputs": inputs, "timeout_s": timeout, "mem_mb": 512, "cpus": 1.0})
    artifacts = []
    for fname, b64 in (res.get("outputs") or {}).items():
        ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else "txt"
        kind, ctype = _KIND.get(ext, ("file", "application/octet-stream"))
        try:
            data = base64.b64decode(b64)
        except (ValueError, TypeError):
            continue
        if not data:
            continue
        artifacts.append(await put_artifact(svc, ctx, kind=kind, title=fname[:120], data=data,
                                            extension=ext, content_type=ctype))
    out = {"exit_code": res.get("exit_code"), "stdout": _cut(res.get("stdout") or ""),
           "stderr": _cut(res.get("stderr") or ""), "artifacts": artifacts}
    if res.get("exit_code") not in (0, None):
        out["hint"] = "O script falhou; leia o stderr, corrija e tente de novo (no máximo 2 vezes)."
    return out
