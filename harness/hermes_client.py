"""Cliente do API server do Hermes (fatos: anexos/04/hermes-agent-fonte.md secao 2)."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from harness.config import Settings, WorkerSpec

log = logging.getLogger("harness.hermes")


class HermesBusy(Exception):
    """429 persistente: worker sem slot."""


class HermesUnavailable(Exception):
    """Sem conexao com o worker (processo morto, rede)."""


class HermesError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"hermes http {status}")
        self.status = status
        self.body = body


@dataclass
class HermesEvent:
    seq: int | None
    event: str
    data: dict


def parse_sse_line_stream(lines: list[str]) -> list[tuple[str | None, str | None, str]]:
    """Utilitario de teste: [(id, event, data)] de linhas SSE ja separadas."""
    out, cur_id, cur_ev, cur_data = [], None, None, []
    for line in lines:
        if line == "":
            if cur_data:
                out.append((cur_id, cur_ev, "\n".join(cur_data)))
            cur_id, cur_ev, cur_data = None, None, []
        elif line.startswith(":"):
            continue
        elif line.startswith("id:"):
            cur_id = line[3:].strip()
        elif line.startswith("event:"):
            cur_ev = line[6:].strip()
        elif line.startswith("data:"):
            cur_data.append(line[5:].lstrip())
    return out


class HermesClient:
    def __init__(self, worker: WorkerSpec, settings: Settings, http: httpx.AsyncClient):
        self.w = worker
        self.s = settings
        self.http = http

    def _base(self, profile: str) -> str:
        return f"{self.w.url}/p/{profile}"

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"Authorization": f"Bearer {self.w.key}"} if self.w.key else {}
        h.update(extra or {})
        return h

    async def start_run(self, profile: str, body: dict, *, idempotency_key: str,
                        session_key: str | None) -> str:
        headers = self._headers({"Idempotency-Key": idempotency_key})
        if session_key:
            headers["X-Hermes-Session-Key"] = session_key
        last = None
        for attempt in range(self.s.hermes_start_retries):
            try:
                r = await self.http.post(f"{self._base(profile)}/v1/runs", json=body,
                                         headers=headers, timeout=30.0)
            except httpx.HTTPError as exc:
                raise HermesUnavailable(str(exc)) from exc
            if r.status_code == 429:
                delay = min(float(r.headers.get("retry-after", "0.3") or 0.3), 2.0)
                last = r
                await asyncio.sleep(max(delay, 0.1) * (attempt + 1))
                continue
            if r.status_code not in (200, 202):
                raise HermesError(r.status_code, r.text[:500])
            return r.json()["run_id"]
        raise HermesBusy(last.text[:200] if last is not None else "429")

    async def stream_events(self, profile: str, hermes_run_id: str,
                            last_seq: int | None) -> AsyncIterator[HermesEvent]:
        """Uma conexao SSE. Termina quando o servidor fecha; o chamador decide reconectar."""
        headers = self._headers({"Accept": "text/event-stream"})
        if last_seq is not None:
            headers["Last-Event-ID"] = str(last_seq)
        url = f"{self._base(profile)}/v1/runs/{hermes_run_id}/events"
        try:
            async with self.http.stream("GET", url, headers=headers,
                                        timeout=httpx.Timeout(10.0, read=None)) as r:
                if r.status_code != 200:
                    raise HermesError(r.status_code, (await r.aread()).decode(errors="replace")[:300])
                cur_id: str | None = None
                cur_ev: str | None = None
                cur_data: list[str] = []
                async for line in r.aiter_lines():
                    if line == "":
                        if cur_data:
                            try:
                                data = json.loads("\n".join(cur_data))
                            except json.JSONDecodeError:
                                data = {"raw": "\n".join(cur_data)}
                            ev = cur_ev or data.get("event") or "message"
                            seq = data.get("seq")
                            if seq is None and cur_id and cur_id.isdigit():
                                seq = int(cur_id)
                            yield HermesEvent(seq, ev, data)
                        cur_id, cur_ev, cur_data = None, None, []
                    elif line.startswith(":"):
                        continue  # keepalive
                    elif line.startswith("id:"):
                        cur_id = line[3:].strip()
                    elif line.startswith("event:"):
                        cur_ev = line[6:].strip()
                    elif line.startswith("data:"):
                        cur_data.append(line[5:].lstrip())
        except httpx.HTTPError as exc:
            raise HermesUnavailable(str(exc)) from exc

    async def resilient_events(self, profile: str, hermes_run_id: str) -> AsyncIterator[HermesEvent]:
        """Reconecta com Last-Event-ID; esgotadas as tentativas -> HermesUnavailable."""
        last_seq: int | None = None
        failures = 0
        while True:
            try:
                got_any = False
                async for ev in self.stream_events(profile, hermes_run_id, last_seq):
                    got_any = True
                    failures = 0
                    if ev.seq is not None:
                        if last_seq is not None and ev.seq <= last_seq:
                            continue  # replay duplicado
                        last_seq = ev.seq
                    yield ev
                    if ev.event in ("run.completed", "run.failed", "run.cancelled",
                                    "run.interrupted"):
                        return
                # servidor fechou sem terminal: tenta de novo
                if not got_any:
                    failures += 1
                else:
                    failures += 1
            except HermesUnavailable:
                failures += 1
            except HermesError as exc:
                if exc.status == 404:  # run desconhecida: worker reiniciou sem o estado
                    raise HermesUnavailable("run desconhecida no worker") from exc
                failures += 1
            if failures > self.s.hermes_reconnect_attempts:
                raise HermesUnavailable("stream de eventos perdido")
            await asyncio.sleep(self.s.hermes_reconnect_backoff_s * failures)

    async def stop(self, profile: str, hermes_run_id: str) -> None:
        try:
            await self.http.post(f"{self._base(profile)}/v1/runs/{hermes_run_id}/stop",
                                 headers=self._headers(), timeout=5.0)
        except httpx.HTTPError as exc:
            log.warning("stop falhou: %s", type(exc).__name__)

    async def approval(self, profile: str, hermes_run_id: str, choice: str,
                       request_id: str | None) -> None:
        body: dict = {"choice": choice}
        if request_id:
            body["request_id"] = request_id
        try:
            await self.http.post(f"{self._base(profile)}/v1/runs/{hermes_run_id}/approval",
                                 json=body, headers=self._headers(), timeout=5.0)
        except httpx.HTTPError as exc:
            log.warning("approval falhou: %s", type(exc).__name__)

    async def steer(self, profile: str, hermes_run_id: str, text: str) -> None:
        try:
            await self.http.post(f"{self._base(profile)}/v1/runs/{hermes_run_id}/steer",
                                 json={"input": text}, headers=self._headers(), timeout=5.0)
        except httpx.HTTPError as exc:
            log.warning("steer falhou: %s", type(exc).__name__)
