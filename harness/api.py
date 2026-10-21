"""API HTTP do kernel-harness (spec 4): runs com SSE, cancel, aprovacoes, validacao de
skills, ensure, broker e saude. Erros: `{detail: <pt-BR>, code}` (contracts/v1/error_response)."""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from jsonschema import Draft202012Validator

from harness import __version__
from harness.events import TERMINAL_EVENTS
from harness.kernel import Kernel
from harness.scheduler import priority_for
from harness.security import (
    AuthError,
    bearer,
    check_run_token_claims,
    token_in,
    verify_run_token,
)
from harness.skillcheck import DEFAULT_TOOL_CATALOG, validate_package
from harness.store import canonical_hash

log = logging.getLogger("harness.api")

MAX_BODY = 1024 * 1024
SSE_KEEPALIVE_S = 15.0
CONTRACTS = Path(__file__).resolve().parent.parent / "contracts" / "v1"


class ApiError(Exception):
    def __init__(self, status: int, code: str, detail: str, problems: list | None = None,
                 headers: dict | None = None):
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail
        self.problems, self.headers = problems, headers or {}


@lru_cache(maxsize=1)
def run_request_validator() -> Draft202012Validator:
    schema = json.loads((CONTRACTS / "run_request.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _err_body(code: str, detail: str, problems: list | None = None) -> dict:
    out = {"detail": detail, "code": code}
    if problems is not None:
        out["problems"] = problems
    return out


def sse(event_id: str, etype: str, data: dict) -> str:
    return f"id: {event_id}\nevent: {etype}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def kernel_of(request: Request) -> Kernel:
    k = getattr(request.app.state, "kernel", None)
    if k is None or not k.started:
        raise ApiError(503, "unavailable", "Serviço iniciando. Tente novamente em instantes.")
    return k


def require_service(request: Request, k: Kernel) -> None:
    if not k.s.service_tokens:
        if k.s.insecure_dev:
            return
        raise ApiError(401, "unauthorized", "Não autorizado.")
    if not token_in(bearer(request.headers.get("authorization")), k.s.service_tokens):
        raise ApiError(401, "unauthorized", "Não autorizado.")


async def read_json(request: Request, limit: int = MAX_BODY) -> dict:
    raw = await request.body()
    if len(raw) > limit:
        raise ApiError(413, "invalid_request", "Corpo da solicitação grande demais.")
    try:
        data = json.loads(raw or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ApiError(422, "invalid_request", "JSON inválido.") from exc
    if not isinstance(data, dict):
        raise ApiError(422, "invalid_request", "O corpo deve ser um objeto JSON.")
    return data


# ------------------------------------------------------------------- SSE
async def event_stream(k: Kernel, request: Request, run_id: str, last_id: str):
    quiet = 0.0
    drained_after_terminal = False
    while True:
        if await request.is_disconnected():
            return
        events = await k.store.read_events(run_id, last_id, block_ms=1000)
        if events:
            quiet = 0.0
            for eid, etype, data in events:
                last_id = eid
                yield sse(eid, etype, data)
                if etype in TERMINAL_EVENTS:
                    return
            continue
        run = await k.store.get_run(run_id) or {}
        if run.get("terminal"):
            if drained_after_terminal:
                return  # terminal sem evento (stream expirou): nao prende o cliente
            drained_after_terminal = True
            continue
        quiet += 1.0
        if quiet >= SSE_KEEPALIVE_S:
            quiet = 0.0
            yield ": keepalive\n\n"


def sse_response(gen, run_id: str) -> StreamingResponse:
    return StreamingResponse(gen, media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Run-Id": run_id})


# ---------------------------------------------------------------- rotas
router = APIRouter()


@router.get("/health")
async def health(request: Request):
    k = getattr(request.app.state, "kernel", None)
    out = {"status": "ok", "service": "kernel-harness", "version": __version__}
    if k is not None:
        out["ready"] = bool(k.started)
        out["workers_healthy"] = sum(1 for w in k.workers.workers.values() if w.healthy)
    return out


@router.get("/version")
async def version():
    import os
    return {"version": __version__, "commit": os.environ.get("GIT_COMMIT", "unknown"),
            "hermes_pinned": os.environ.get("HERMES_IMAGE", "nousresearch/hermes-agent:v0.22.2"),
            "contract_version": 1}


@router.post("/v1/runs")
async def create_run(request: Request):
    k = kernel_of(request)
    require_service(request, k)
    body = await read_json(request)
    if body.get("contract_version") != 1:
        raise ApiError(422, "unsupported_contract", "Versão de contrato não suportada.")
    problems = [{"path": "/".join(str(p) for p in e.absolute_path), "message": e.message[:200]}
                for e in sorted(run_request_validator().iter_errors(body),
                                key=lambda e: list(e.absolute_path))][:20]
    if problems:
        raise ApiError(422, "invalid_request", "Solicitação inválida.", problems)

    if k.s.run_token_required or body.get("run_token"):
        try:
            if not k.s.run_token_secret:
                raise AuthError("bad_run_token", "run_token não configurado", 401)
            check_run_token_claims(verify_run_token(body["run_token"], k.s.run_token_secret), body)
        except AuthError as exc:
            raise ApiError(exc.status, exc.code, exc.detail) from exc

    tenant_id, run_id = body["tenant_id"], body["run_id"]
    if k.scheduler.over_capacity(tenant_id):
        raise ApiError(429, "backpressure", "Muitas solicitações no momento. Tente novamente em "
                       "instantes.", headers={"Retry-After": "5"})
    state, existing = await k.store.claim_idempotency(
        tenant_id, body["idempotency_key"], canonical_hash(body), run_id)
    if state == "conflict":
        raise ApiError(409, "idempotency_conflict",
                       "Esta chave de idempotência já foi usada com outro conteúdo.")
    if state == "replay":
        if not await k.store.get_run(existing):
            raise ApiError(404, "not_found", "Execução não encontrada.")
        return sse_response(event_stream(k, request, existing, "0"), existing)

    priority = priority_for(body.get("channel", "web"))
    if not await k.store.create_run(body, priority):
        await k.store.release_idempotency(tenant_id, body["idempotency_key"])
        raise ApiError(409, "idempotency_conflict", "Esta execução já existe.")
    await k.scheduler.submit(body, priority)
    return sse_response(event_stream(k, request, run_id, "0"), run_id)


async def _own_run(k: Kernel, run_id: str, tenant_id: str | None) -> dict:
    run = await k.store.get_run(run_id)
    if run is None or (tenant_id and run.get("tenant_id") != tenant_id):
        raise ApiError(404, "not_found", "Execução não encontrada.")
    return run


@router.get("/v1/runs/{run_id}/events")
async def run_events(run_id: str, request: Request, tenant_id: str | None = None):
    k = kernel_of(request)
    require_service(request, k)
    await _own_run(k, run_id, tenant_id)
    last = request.headers.get("last-event-id") or "0"
    return sse_response(event_stream(k, request, run_id, last), run_id)


@router.get("/v1/runs/{run_id}")
async def run_status(run_id: str, request: Request, tenant_id: str | None = None):
    k = kernel_of(request)
    require_service(request, k)
    run = await _own_run(k, run_id, tenant_id)
    return {"run_id": run_id, "status": run.get("status"), "error_code": run.get("error_code"),
            "terminal": bool(run.get("terminal"))}


@router.post("/v1/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request):
    k = kernel_of(request)
    require_service(request, k)
    body = await read_json(request, 8192)
    run = await _own_run(k, run_id, body.get("tenant_id"))
    if run.get("terminal"):
        return {"status": "already_finished"}
    await k.store.set_run(run_id, cancel=1)
    if await k.scheduler.cancel_queued(run_id):
        return {"status": "cancelled"}
    ctx = k.contexts.by_run(run_id)
    if ctx is not None:
        ctx.cancelled.set()
    return {"status": "cancelling"}


@router.post("/v1/approvals/{approval_id}")
async def decide_approval(approval_id: str, request: Request):
    k = kernel_of(request)
    require_service(request, k)
    body = await read_json(request, 8192)
    if body.get("decision") not in ("approve", "deny"):
        raise ApiError(422, "invalid_request", "Decisão inválida.")
    status, payload = await k.approvals.decide(approval_id, body["decision"],
                                               tenant_id=body.get("tenant_id"),
                                               user_id=body.get("user_id"))
    return JSONResponse(payload, status_code=status)


@router.post("/v1/skills/validate")
async def skills_validate(request: Request):
    k = kernel_of(request)
    require_service(request, k)
    body = await read_json(request, 4 * 1024 * 1024)
    files = body.get("files")
    if not isinstance(files, list) or not all(isinstance(f, dict) for f in files):
        raise ApiError(422, "invalid_request", "Lista de arquivos inválida.")
    avail = set(body["available_tools"]) if body.get("available_tools") is not None else None
    rep = validate_package(files, body.get("manifest") or {}, avail or DEFAULT_TOOL_CATALOG)
    return {"ok": rep.ok, "problems": [p.dict() for p in rep.problems]}


@router.post("/v1/tenants/{tenant_id}/ensure")
async def ensure_tenant(tenant_id: str, request: Request):
    k = kernel_of(request)
    require_service(request, k)
    await k.workers.probe_all()
    w = await k.workers.assign(tenant_id)
    return {"tenant_id": tenant_id, "ready": w is not None, "worker": w.id if w else None}


@router.post("/v1/memories/extract")
async def memories_extract(request: Request):
    from harness import memories

    k = kernel_of(request)
    require_service(request, k)
    body = await read_json(request, 512 * 1024)
    msgs = body.get("messages")
    if not isinstance(msgs, list) or not body.get("tenant_id"):
        raise ApiError(422, "invalid_request", "Informe tenant_id e messages.")
    facts = await memories.extract(
        k.s, k.http, messages=[m for m in msgs if isinstance(m, dict)],
        existing=[str(x) for x in body.get("existing") or []],
        model=str((body.get("model") or {}).get("alias") or ""),
        secrets=[str(x) for x in body.get("secret_values") or []],
        gateway=(body.get("model") or {}).get("gateway"))
    return {"facts": facts}


@router.get("/v1/stats")
async def stats(request: Request):
    k = kernel_of(request)
    require_service(request, k)
    return {"scheduler": k.scheduler.stats(), "workers": k.workers.snapshot(),
            "active_runs": k.contexts.active(), "dispatcher": k.dispatcher.stats,
            "broker": k.broker.stats}


@router.post("/broker/v1/call")
async def broker_call(request: Request):
    k = kernel_of(request)
    body = await read_json(request, 512 * 1024)
    status, payload = await k.broker.call(bearer(request.headers.get("authorization")), body)
    return JSONResponse(payload, status_code=status)


# --------------------------------------------------------------- fabrica
def create_app(settings=None, kernel: Kernel | None = None) -> FastAPI:
    from contextlib import asynccontextmanager

    from harness.config import Settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        k = kernel or Kernel(settings or Settings.from_env())
        app.state.kernel = k
        await k.start()
        try:
            yield
        finally:
            await k.stop()

    app = FastAPI(title="kernel-harness", version=__version__, lifespan=lifespan)

    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, exc: ApiError):
        return JSONResponse(_err_body(exc.code, exc.detail, exc.problems),
                            status_code=exc.status, headers=exc.headers)

    app.include_router(router)
    return app

