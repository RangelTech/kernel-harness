"""Front do worker efemero: mesmo contrato HTTP do API server do Hermes, sem estado por perfil.

Recebe `POST /p/<perfil>/v1/runs`, pede ao zigoto um filho para ESSA run (HERMES_HOME montado so
agora), repassa o pedido e os eventos (SSE) e mata o filho depois do evento terminal. Nao importa o
Hermes (fica leve: ~30 MB, CPU ~0 parado). Endpoints repassados: runs, events, stop, approval, steer,
GET run. `/health` e proprio.

Sem TTL por ociosidade: o filho morre porque a run acabou (ou por prazo maximo da run), nunca por
"ficou parado".
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import aiohttp
from aiohttp import web

log = logging.getLogger("rt_worker.front")

PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
TERMINAL = (b'"event": "run.completed"', b'"event": "run.failed"', b'"event": "run.cancelled"',
            b'"event": "run.interrupted"')
HOP = {"connection", "keep-alive", "transfer-encoding", "content-length", "host", "upgrade"}


@dataclass
class Config:
    slots: int = 16
    profiles_root: str = "/opt/data/profiles"
    sock: str = "/tmp/rt-zygote.sock"
    port_lo: int = 21000
    port_hi: int = 21999
    child_boot_timeout_s: float = 20.0
    run_max_s: float = 3600.0  # teto da run (o timeout de turno de verdade e do kernel)
    grace_s: float = 15.0  # depois do evento terminal, o filho espera releituras (Last-Event-ID)
    kill_after_s: float = 5.0
    sweep_s: float = 2.0

    @classmethod
    def from_env(cls) -> Config:
        e = os.environ
        return cls(
            slots=int(e.get("RT_WORKER_SLOTS", 16)),
            profiles_root=e.get("PROFILES_ROOT", cls.profiles_root),
            sock=e.get("RT_WORKER_SOCK", cls.sock),
            run_max_s=float(e.get("RT_WORKER_RUN_MAX_S", 3600)),
            grace_s=float(e.get("RT_WORKER_GRACE_S", 15)),
            child_boot_timeout_s=float(e.get("RT_WORKER_BOOT_TIMEOUT_S", 20)),
        )


class Spawner(Protocol):
    async def spawn(self, profile: str, port: int, deadline_s: float) -> int: ...
    def alive(self, pid: int) -> bool: ...
    def ready(self) -> bool: ...
    def kill(self, pid: int, sig: int) -> None: ...


class ZygoteSpawner:
    def __init__(self, sock: str):
        self.sock = sock

    async def spawn(self, profile: str, port: int, deadline_s: float) -> int:
        r, w = await asyncio.open_unix_connection(self.sock)
        try:
            w.write(json.dumps({"profile": profile, "port": port, "deadline_s": deadline_s})
                    .encode() + b"\n")
            await w.drain()
            data = json.loads((await asyncio.wait_for(r.readline(), 10)).decode())
        finally:
            w.close()
        if "pid" not in data:
            raise RuntimeError(f"zigoto recusou: {data.get('error')}")
        return int(data["pid"])

    def ready(self) -> bool:
        """O zigoto so cria o socket depois de aquecer: antes disso o worker nao esta pronto."""
        return os.path.exists(self.sock)

    def alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def kill(self, pid: int, sig: int) -> None:
        try:
            os.killpg(pid, sig)  # o filho e lider de sessao/grupo (setsid)
        except OSError:
            try:
                os.kill(pid, sig)
            except OSError:
                pass


@dataclass
class RunRec:
    run_id: str
    profile: str
    port: int
    pid: int
    started: float = field(default_factory=time.monotonic)
    terminal_at: float | None = None
    idem: str = ""
    kill_sent_at: float | None = None


class Front:
    def __init__(self, cfg: Config, spawner: Spawner):
        self.cfg = cfg
        self.sp = spawner
        self.runs: dict[str, RunRec] = {}
        self.idem: dict[tuple[str, str], str] = {}
        self.starting = 0
        self.ports_free = list(range(cfg.port_lo, cfg.port_hi + 1))
        self.http: aiohttp.ClientSession | None = None
        self.stats = {"started": 0, "rejected": 0, "spawn_failed": 0, "boot_ms_last": 0.0,
                      "boot_ms_max": 0.0}
        self._sweeper: asyncio.Task | None = None

    # ------------------------------------------------------------ ciclo
    async def start(self) -> None:
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=3))
        self._sweeper = asyncio.create_task(self._sweep_loop())

    async def stop(self) -> None:
        if self._sweeper:
            self._sweeper.cancel()
        for rec in list(self.runs.values()):
            self.sp.kill(rec.pid, signal.SIGTERM)
        if self.http:
            await self.http.close()

    @property
    def active(self) -> int:
        """Runs que ainda ocupam slot: em andamento ou subindo. Filho que ja deu o evento terminal e
        so espera releitura (graca) NAO ocupa slot; so memoria, limitada por `max_children`."""
        return sum(1 for r in self.runs.values() if r.terminal_at is None) + self.starting

    def _release(self, rec: RunRec) -> None:
        self.runs.pop(rec.run_id, None)
        if rec.idem:
            self.idem.pop((rec.profile, rec.idem), None)
        self.ports_free.append(rec.port)

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.sweep_s)
            try:
                self.sweep()
            except Exception:  # noqa: BLE001
                log.exception("sweep falhou")

    def sweep(self) -> None:
        now = time.monotonic()
        for rec in list(self.runs.values()):
            dead = not self.sp.alive(rec.pid)
            if dead:
                self._release(rec)
                continue
            if rec.kill_sent_at is not None:
                if now - rec.kill_sent_at >= self.cfg.kill_after_s:
                    self.sp.kill(rec.pid, signal.SIGKILL)
                continue
            over_grace = rec.terminal_at is not None and now - rec.terminal_at >= self.cfg.grace_s
            over_max = now - rec.started >= self.cfg.run_max_s + self.cfg.grace_s
            if over_grace or over_max:
                if over_max:
                    log.warning("run %s passou do teto (%ss): encerrando", rec.run_id,
                                self.cfg.run_max_s)
                rec.kill_sent_at = now
                self.sp.kill(rec.pid, signal.SIGTERM)

    # ------------------------------------------------------------ rotas
    def app(self) -> web.Application:
        app = web.Application(client_max_size=12 * 1024 * 1024)
        app.router.add_get("/health", self.h_health)
        app.router.add_post("/p/{profile}/v1/runs", self.h_start)
        app.router.add_route("*", "/p/{profile}/v1/runs/{run_id}", self.h_proxy)
        app.router.add_route("*", "/p/{profile}/v1/runs/{run_id}/{tail}", self.h_proxy)
        return app

    async def h_health(self, request: web.Request) -> web.Response:
        ready = self.sp.ready()
        return web.json_response({
            "status": "ok" if ready else "warming", "mode": "ephemeral", "active": self.active, "slots": self.cfg.slots,
            "runs": len(self.runs), **self.stats},
            status=200 if ready else 503)

    def _profile_ok(self, profile: str) -> bool:
        return bool(PROFILE_RE.match(profile)) and (
            Path(self.cfg.profiles_root) / profile / "config.yaml").is_file()

    async def h_start(self, request: web.Request) -> web.StreamResponse:
        profile = request.match_info["profile"]
        if not self._profile_ok(profile):
            return web.json_response({"error": "Unknown or unconfigured profile"}, status=404)
        if not request.headers.get("Authorization"):
            return web.json_response({"error": "missing bearer"}, status=401)
        idem = request.headers.get("Idempotency-Key", "").strip()
        if idem and (rid := self.idem.get((profile, idem))) and rid in self.runs:
            return web.json_response({"run_id": rid, "status": "started", "replayed": True},
                                     status=202)
        if (self.active >= self.cfg.slots or not self.ports_free
                or len(self.runs) + self.starting >= self.cfg.slots * 2):
            self.stats["rejected"] += 1
            return web.json_response({"error": "worker sem slot", "code": "worker_busy"},
                                     status=429, headers={"Retry-After": "1"})
        body = await request.read()
        port = self.ports_free.pop()
        self.starting += 1
        t0 = time.monotonic()
        pid = None
        try:
            pid = await self.sp.spawn(profile, port, self.cfg.run_max_s + self.cfg.grace_s + 30)
            await self._wait_ready(port, pid)
            boot_ms = (time.monotonic() - t0) * 1000
            self.stats["boot_ms_last"] = round(boot_ms, 1)
            self.stats["boot_ms_max"] = max(self.stats["boot_ms_max"], round(boot_ms, 1))
            assert self.http is not None
            headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
            async with self.http.post(f"http://127.0.0.1:{port}/p/{profile}/v1/runs", data=body,
                                      headers=headers,
                                      timeout=aiohttp.ClientTimeout(total=30)) as r:
                payload = await r.read()
                status = r.status
                ctype = r.headers.get("Content-Type", "application/json")
            run_id = ""
            if status in (200, 202):
                run_id = json.loads(payload).get("run_id", "")
            if not run_id:
                self.sp.kill(pid, signal.SIGTERM)
                self.ports_free.append(port)
                return web.Response(body=payload, status=status, content_type=ctype.split(";")[0])
            self.runs[run_id] = RunRec(run_id, profile, port, pid, idem=idem)
            if idem:
                self.idem[(profile, idem)] = run_id
            self.stats["started"] += 1
            return web.Response(body=payload, status=status, content_type=ctype.split(";")[0])
        except Exception as exc:  # noqa: BLE001
            self.stats["spawn_failed"] += 1
            log.warning("falha ao iniciar run (%s): %s", profile, type(exc).__name__)
            if pid is not None:
                self.sp.kill(pid, signal.SIGKILL)
            self.ports_free.append(port)
            return web.json_response({"error": "worker nao conseguiu iniciar a run",
                                      "code": "spawn_failed"}, status=503,
                                     headers={"Retry-After": "1"})
        finally:
            self.starting -= 1

    async def _wait_ready(self, port: int, pid: int) -> None:
        assert self.http is not None
        end = time.monotonic() + self.cfg.child_boot_timeout_s
        delay = 0.02
        while time.monotonic() < end:
            if not self.sp.alive(pid):
                raise RuntimeError("filho morreu no boot")
            try:
                async with self.http.get(f"http://127.0.0.1:{port}/health",
                                         timeout=aiohttp.ClientTimeout(total=1.5)) as r:
                    if r.status == 200:
                        return
            except (aiohttp.ClientError, TimeoutError):
                pass
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 0.2)
        raise TimeoutError("filho nao ficou pronto")

    async def h_proxy(self, request: web.Request) -> web.StreamResponse:
        profile = request.match_info["profile"]
        run_id = request.match_info["run_id"]
        rec = self.runs.get(run_id)
        if rec is None or rec.profile != profile:
            return web.json_response({"error": "run unknown"}, status=404)
        assert self.http is not None
        url = f"http://127.0.0.1:{rec.port}{request.rel_url}"
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
        data = await request.read() if request.can_read_body else None
        try:
            async with self.http.request(
                    request.method, url, data=data, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=None, sock_connect=3,
                                                  sock_read=None)) as r:
                resp = web.StreamResponse(status=r.status)
                for k, v in r.headers.items():
                    if k.lower() not in HOP:
                        resp.headers[k] = v
                await resp.prepare(request)
                tail = b""
                async for chunk in r.content.iter_any():
                    window = tail + chunk
                    if any(t in window for t in TERMINAL) and rec.terminal_at is None:
                        rec.terminal_at = time.monotonic()
                    tail = window[-64:]
                    await resp.write(chunk)
                await resp.write_eof()
                return resp
        except (aiohttp.ClientError, TimeoutError):
            return web.json_response({"error": "run indisponivel"}, status=502)


async def amain() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = Config.from_env()
    front = Front(cfg, ZygoteSpawner(cfg.sock))
    await front.start()
    runner = web.AppRunner(front.app())
    await runner.setup()
    site = web.TCPSite(runner, os.environ.get("API_SERVER_HOST", "0.0.0.0"),
                       int(os.environ.get("API_SERVER_PORT", "8642")))
    await site.start()
    log.info("front efemero em :%s (slots=%s)", os.environ.get("API_SERVER_PORT", "8642"),
             cfg.slots)
    await asyncio.Event().wait()
