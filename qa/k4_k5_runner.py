"""Evidência reproduzível para K-4/K-5, sem segredo persistido.

Este arquivo não faz deploy, não reinicia containers e não executa carga por
import. O operador fornece os segredos somente pelo ambiente do processo.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

TERMINAL_EVENTS = {"done", "error"}


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def sign_backend_run_token(claims: dict[str, Any], secret: str) -> str:
    """Assina no formato de dois segmentos emitido pelo backend real."""
    body = b64url(json.dumps(claims, separators=(",", ":")).encode())
    key = hashlib.sha256(("run-token:" + secret).encode()).digest()
    signature = b64url(hmac.new(key, body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}"


@dataclass(frozen=True)
class Settings:
    base_url: str
    service_token: str
    run_token_secret: str
    template_id: str
    template_version: str
    model_alias: str
    message: str
    timeout_s: float

    @classmethod
    def from_env(cls, timeout_s: float) -> Settings:
        names = ("HARNESS_QA_BASE_URL", "HARNESS_QA_SERVICE_TOKEN", "HARNESS_QA_RUN_TOKEN_SECRET")
        missing = [name for name in names if not os.environ.get(name)]
        if missing:
            raise ValueError("faltam variáveis: " + ", ".join(missing))
        return cls(
            base_url=os.environ["HARNESS_QA_BASE_URL"].rstrip("/"),
            service_token=os.environ["HARNESS_QA_SERVICE_TOKEN"],
            run_token_secret=os.environ["HARNESS_QA_RUN_TOKEN_SECRET"],
            template_id=os.environ.get("HARNESS_QA_TEMPLATE_ID", "qa-k4-k5"),
            template_version=os.environ.get("HARNESS_QA_TEMPLATE_VERSION", "qa"),
            model_alias=os.environ.get("HARNESS_QA_MODEL_ALIAS", "agente-padrao"),
            message=os.environ.get("HARNESS_QA_MESSAGE", "Responda somente: QA K4 K5 OK."),
            timeout_s=timeout_s,
        )


def build_body(settings: Settings, *, tenant_id: str, index: int) -> dict[str, Any]:
    run_id = f"kqa-{uuid.uuid4().hex}"
    user_id = f"qa-user-{index % 50}"
    body: dict[str, Any] = {
        "contract_version": 1,
        "run_id": run_id,
        "idempotency_key": f"kqa-{run_id}",
        "tenant_id": tenant_id,
        "user_id": user_id,
        "actor": {"type": "member", "name": "QA K4/K5"},
        "chat_id": f"qa-k4-k5-{index}",
        "channel": "api",
        "message_id": f"m-{run_id}",
        "template": {"id": settings.template_id, "version_id": settings.template_version, "kind": "skills"},
        "message": settings.message,
        "attachments": [],
        "history": [],
        "model": {"alias": settings.model_alias, "reasoning_effort": "low"},
        "skills": {"system_rev": "qa-k4-k5", "tenant_package": {"sha256": "qa-k4-k5"}, "user_skills": []},
        "manifest": {"tools": [], "limits": {"tenant_concurrency": 2}},
        "credentials": {},
        "credentials_catalog": [],
        "memory": {"recall": []},
    }
    body["run_token"] = sign_backend_run_token(
        {"run_id": run_id, "tenant_id": tenant_id, "user_id": user_id, "exp": time.time() + settings.timeout_s + 120},
        settings.run_token_secret,
    )
    return body


@dataclass
class Observation:
    run_id: str
    tenant_id: str
    elapsed_s: float
    first_event_s: float | None
    terminal_event: str | None
    status: str | None
    error_code: str | None
    http_status: int | None


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * percent))]


class Runner:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.headers = {"Authorization": f"Bearer {settings.service_token}"}

    async def one(self, client: httpx.AsyncClient, *, tenant_id: str, index: int,
                  first_event: asyncio.Event | None = None,
                  on_submitted: Callable[[str, str], None] | None = None) -> Observation:
        body = build_body(self.settings, tenant_id=tenant_id, index=index)
        if on_submitted:
            on_submitted(body["run_id"], tenant_id)
        started = time.perf_counter()
        first_at: float | None = None
        terminal_event: str | None = None
        error_code: str | None = None
        http_status: int | None = None
        try:
            async with client.stream("POST", f"{self.settings.base_url}/v1/runs", json=body,
                                     headers=self.headers) as response:
                http_status = response.status_code
                if response.status_code != 200:
                    return Observation(body["run_id"], tenant_id, time.perf_counter() - started, None,
                                       None, None, "http_error", http_status)
                event_type = ""
                data: list[str] = []
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data.append(line[5:].strip())
                    elif not line and event_type:
                        if first_at is None:
                            first_at = time.perf_counter() - started
                            if first_event:
                                first_event.set()
                        if event_type in TERMINAL_EVENTS:
                            terminal_event = event_type
                            if event_type == "error":
                                try:
                                    error_code = json.loads("\n".join(data)).get("code")
                                except json.JSONDecodeError:
                                    error_code = "invalid_sse_error"
                            break
                        event_type, data = "", []
        except (httpx.HTTPError, TimeoutError) as exc:
            return Observation(body["run_id"], tenant_id, time.perf_counter() - started, first_at,
                               terminal_event, None, type(exc).__name__, http_status)
        status, code = await self.status(client, body["run_id"], tenant_id)
        return Observation(body["run_id"], tenant_id, time.perf_counter() - started, first_at,
                           terminal_event, status, error_code or code, http_status)

    async def status(self, client: httpx.AsyncClient, run_id: str, tenant_id: str) -> tuple[str | None, str | None]:
        try:
            response = await client.get(f"{self.settings.base_url}/v1/runs/{run_id}",
                                        params={"tenant_id": tenant_id}, headers=self.headers)
            if response.status_code != 200:
                return None, f"status_http_{response.status_code}"
            data = response.json()
            return data.get("status"), data.get("error_code")
        except (httpx.HTTPError, ValueError) as exc:
            return None, type(exc).__name__


def report(mode: str, observations: list[Observation], *, elapsed_s: float, args: argparse.Namespace) -> dict[str, Any]:
    first = [item.first_event_s for item in observations if item.first_event_s is not None]
    succeeded = [item for item in observations if item.status == "completed" and item.terminal_event == "done"]
    errors = [item for item in observations if item not in succeeded]
    return {
        "schema": "k4-k5-qa/v1",
        "mode": mode,
        "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - elapsed_s)),
        "elapsed_s": round(elapsed_s, 3),
        "parameters": {"concurrency": getattr(args, "concurrency", 1), "tenants": getattr(args, "tenants", 1)},
        "counts": {"submitted": len(observations), "completed": len(succeeded), "not_completed": len(errors)},
        "success_ratio": round(len(succeeded) / len(observations), 6) if observations else 0.0,
        "first_event_s": {"p50": percentile(first, .5), "p95": percentile(first, .95)},
        "runs": [asdict(item) for item in observations],
    }


def write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


async def soak(settings: Settings, args: argparse.Namespace) -> dict[str, Any]:
    deadline = time.monotonic() + args.duration_s
    observations: list[Observation] = []
    lock = asyncio.Lock()

    async def worker(slot: int, client: httpx.AsyncClient) -> None:
        attempt = slot
        while time.monotonic() < deadline:
            tenant = f"{args.tenant_prefix}-{attempt % args.tenants}"
            item = await Runner(settings).one(client, tenant_id=tenant, index=attempt)
            async with lock:
                observations.append(item)
            attempt += args.concurrency

    started = time.perf_counter()
    timeout = httpx.Timeout(settings.timeout_s + 30)
    async with httpx.AsyncClient(timeout=timeout) as client:
        await asyncio.gather(*(worker(slot, client) for slot in range(args.concurrency)))
    return report("soak", observations, elapsed_s=time.perf_counter() - started, args=args)


async def in_flight(settings: Settings, args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    first_event = asyncio.Event()
    submitted: dict[str, str | None] = {"run_id": None, "tenant_id": None}

    def on_submitted(run_id: str, tenant_id: str) -> None:
        submitted.update(run_id=run_id, tenant_id=tenant_id)

    timeout = httpx.Timeout(settings.timeout_s + 30)
    async with httpx.AsyncClient(timeout=timeout) as client:
        task = asyncio.create_task(Runner(settings).one(client, tenant_id=args.tenant_prefix, index=0,
                                                        first_event=first_event, on_submitted=on_submitted))
        try:
            await asyncio.wait_for(first_event.wait(), timeout=args.ready_timeout_s)
        except TimeoutError:
            pass
        # O arquivo é o ponto de sincronização para o operador matar o worker escolhido.
        args.ready_file.parent.mkdir(parents=True, exist_ok=True)
        args.ready_file.write_text(json.dumps({"run_id": submitted["run_id"], "tenant_id": submitted["tenant_id"],
                                                "first_event_seen": first_event.is_set()}, indent=2) + "\n",
                                   encoding="utf-8")
        observed = await task
    output = report("in-flight", [observed], elapsed_s=time.perf_counter() - started, args=args)
    output["expectation"] = "interrupted" if args.expect_interrupted else "record-only"
    output["pass"] = (observed.status == "interrupted" and observed.error_code == "interrupted") if args.expect_interrupted else None
    return output


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=("soak", "in-flight"))
    p.add_argument("--out", type=Path, required=True, help="JSON de evidência, nunca inclui segredos")
    p.add_argument("--timeout-s", type=float, default=900.0)
    p.add_argument("--tenant-prefix", default="qa-k4-k5")
    p.add_argument("--tenants", type=int, default=10)
    p.add_argument("--concurrency", type=int, default=15)
    p.add_argument("--duration-s", type=float, default=1800.0)
    p.add_argument("--ready-file", type=Path, default=Path("k5-kill-ready.json"))
    p.add_argument("--ready-timeout-s", type=float, default=120.0)
    p.add_argument("--expect-interrupted", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.tenants < 1 or args.concurrency < 1 or args.duration_s <= 0 or args.timeout_s <= 0:
        raise SystemExit("tenants, concurrency, duration-s e timeout-s devem ser positivos")
    settings = Settings.from_env(args.timeout_s)
    payload = asyncio.run(soak(settings, args) if args.mode == "soak" else in_flight(settings, args))
    write_report(args.out, payload)
    print(json.dumps({"out": str(args.out), "mode": args.mode, "success_ratio": payload["success_ratio"]}))
    return 0 if payload.get("pass") is not False else 2


if __name__ == "__main__":
    sys.exit(main())
