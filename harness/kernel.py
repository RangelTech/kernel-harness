"""Montagem dos componentes do kernel-harness (injecao de dependencias para teste)."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx
import redis.asyncio as aioredis

from broker.service import BrokerService
from harness.approvals import ApprovalService
from harness.backend import Backend, HttpBackend
from harness.config import Settings
from harness.dispatcher import Dispatcher
from harness.events import EventBus
from harness.profiles import ProfileGenerator, ProfileManager
from harness.recovery import recover_orphans
from harness.runctx import RunContexts
from harness.scheduler import PendingRuns, Scheduler
from harness.store import Store
from harness.workers import Fencer, WorkerRegistry

log = logging.getLogger("harness.kernel")


def discover_system_skills(settings: Settings) -> frozenset[str]:
    root = Path(settings.system_skills_dir)
    if not root.is_dir():
        return frozenset()
    return frozenset(p.parent.name for p in root.rglob("SKILL.md"))


class Kernel:
    def __init__(self, settings: Settings, *, redis: aioredis.Redis | None = None,
                 backend: Backend | None = None, http: httpx.AsyncClient | None = None,
                 worker_http: httpx.AsyncClient | None = None,
                 generator: ProfileGenerator | None = None, fencer: Fencer | None = None,
                 email_transport=None, resolver=None):
        self.s = settings
        self.http = http or httpx.AsyncClient(timeout=30.0)
        self._own_http = http is None
        self.worker_http = worker_http or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        self._own_worker_http = worker_http is None
        self.redis = redis or aioredis.from_url(settings.redis_url, decode_responses=True)
        self.store = Store(self.redis, settings)
        self.bus = EventBus(self.store)
        self.backend: Backend = backend or HttpBackend(settings, self.http)
        self.contexts = RunContexts()
        self.workers = WorkerRegistry(settings, self.store, fencer=fencer, http=worker_http)
        self.approvals = ApprovalService(settings, self.store, self.backend, self.bus,
                                         self.contexts)
        self.profiles = ProfileManager(settings, self.backend, generator)
        self.system_skills = discover_system_skills(settings)
        self.dispatcher = Dispatcher(settings, self.store, self.bus, self.workers, self.contexts,
                                     self.profiles, self.backend, self.approvals,
                                     self.worker_http, self.system_skills)
        self.pending = PendingRuns()
        self.scheduler = Scheduler(settings, self.store, self.bus, self.workers, self.pending,
                                   self.dispatcher.execute)
        self.broker = BrokerService(settings, self.contexts, self.workers, self.approvals,
                                    self.backend, self.http, email_transport, resolver)
        self.started = False
        self._boot_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Sobe tudo. Se o Redis ainda nao responde, o processo fica de pe (health ok,
        ready=false, runs com 503) e tenta de novo em segundo plano."""
        try:
            await self._bring_up()
        except (aioredis.ConnectionError, aioredis.TimeoutError, OSError) as exc:
            log.warning("Redis indisponivel no boot (%s); tentando em segundo plano",
                        type(exc).__name__)
            self._boot_task = asyncio.create_task(self._boot_loop())

    async def _bring_up(self) -> None:
        await self.redis.ping()
        await recover_orphans(self.store, self.bus)
        await self.workers.probe_all()
        await self.workers.start()
        await self.scheduler.start()
        self.started = True

    async def _boot_loop(self) -> None:
        while True:
            await asyncio.sleep(self.s.boot_retry_s)
            try:
                await self._bring_up()
                log.info("Redis voltou; kernel pronto")
                return
            except (aioredis.ConnectionError, aioredis.TimeoutError, OSError):
                continue

    async def stop(self) -> None:
        self.started = False
        if self._boot_task is not None:
            self._boot_task.cancel()
            await asyncio.gather(self._boot_task, return_exceptions=True)
        await self.scheduler.stop()
        await self.workers.stop()
        await self.backend.aclose()
        if self._own_http:
            await self.http.aclose()
        if self._own_worker_http:
            await self.worker_http.aclose()
        await self.redis.aclose()
