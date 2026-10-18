"""Registro de workers Hermes: afinidade empresa->worker, saude, slots, fencing."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from harness.config import Settings, WorkerSpec
from harness.store import Store

log = logging.getLogger("harness.workers")


class Fencer(Protocol):
    async def fence(self, worker_id: str, tenant_id: str) -> None:
        """Garante que `worker_id` parou de escrever nos perfis de `tenant_id` (RM)."""


class NoopFencer:
    """Padrao em dev/teste: so registra. Em producao o RM implementa o fencing real."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def fence(self, worker_id: str, tenant_id: str) -> None:
        self.calls.append((worker_id, tenant_id))
        log.warning("fencing solicitado worker=%s tenant=%s", worker_id, tenant_id)


@dataclass
class Worker:
    spec: WorkerSpec
    healthy: bool = True
    in_use: int = 0
    fail_reason: str = ""
    runs_total: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def free(self) -> int:
        return self.spec.slots - self.in_use


def hrw_score(tenant_id: str, worker_id: str) -> int:
    """Rendezvous hashing: hash estavel; sair um worker so move os tenants dele."""
    return int.from_bytes(hashlib.sha256(f"{tenant_id}|{worker_id}".encode()).digest()[:8], "big")


class WorkerRegistry:
    def __init__(self, settings: Settings, store: Store, fencer: Fencer | None = None,
                 http: httpx.AsyncClient | None = None):
        self.s = settings
        self.store = store
        self.fencer = fencer or NoopFencer()
        self.workers: dict[str, Worker] = {w.id: Worker(w) for w in settings.workers}
        self._affinity: dict[str, str] = {}
        self._http = http or httpx.AsyncClient(timeout=3.0)
        self._own_http = http is None
        self._task: asyncio.Task | None = None
        self._assign_lock = asyncio.Lock()

    # ------------------------------------------------------------ saude
    async def start(self) -> None:
        self._task = asyncio.create_task(self._health_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self._own_http:
            await self._http.aclose()

    async def _health_loop(self) -> None:
        while True:
            await self.probe_all()
            await asyncio.sleep(self.s.worker_health_interval_s)

    async def probe_all(self) -> None:
        await asyncio.gather(*(self.probe(w) for w in self.workers.values()),
                             return_exceptions=True)

    async def probe(self, w: Worker) -> bool:
        try:
            r = await self._http.get(f"{w.spec.url}/health", timeout=3.0)
            ok = r.status_code == 200
            reason = "" if ok else f"http {r.status_code}"
        except httpx.HTTPError as exc:
            ok, reason = False, type(exc).__name__
        if ok and not w.healthy:
            log.info("worker %s voltou", w.id)
        if not ok and w.healthy:
            log.warning("worker %s fora: %s", w.id, reason)
        w.healthy, w.fail_reason = ok, reason
        return ok

    def mark_unhealthy(self, worker_id: str, reason: str) -> None:
        w = self.workers.get(worker_id)
        if w and w.healthy:
            w.healthy = False
            w.fail_reason = reason
            log.warning("worker %s marcado fora: %s", worker_id, reason)

    # -------------------------------------------------------- afinidade
    def _pick(self, tenant_id: str) -> Worker | None:
        healthy = [w for w in self.workers.values() if w.healthy]
        if not healthy:
            return None
        return max(healthy, key=lambda w: hrw_score(tenant_id, w.id))

    async def assign(self, tenant_id: str) -> Worker | None:
        """Worker do tenant (um unico escritor dos state.db). Troca so se o atual caiu."""
        async with self._assign_lock:
            current_id = self._affinity.get(tenant_id) or await self.store.get_affinity(tenant_id)
            current = self.workers.get(current_id) if current_id else None
            if current is not None and current.healthy:
                self._affinity[tenant_id] = current.id
                return current
            new = self._pick(tenant_id)
            if new is None:
                return None
            if current is not None and current.id != new.id:
                # failover: fencing do antigo ANTES de o novo escrever nos perfis
                await self.fencer.fence(current.id, tenant_id)
                log.warning("tenant %s: %s -> %s (failover)", tenant_id, current.id, new.id)
            self._affinity[tenant_id] = new.id
            await self.store.set_affinity(tenant_id, new.id)
            return new

    def affinity_of(self, tenant_id: str) -> str | None:
        return self._affinity.get(tenant_id)

    # ------------------------------------------------------------ slots
    def try_acquire(self, w: Worker) -> bool:
        if w.in_use >= w.spec.slots:
            return False
        w.in_use += 1
        w.runs_total += 1
        return True

    def release(self, worker_id: str) -> None:
        w = self.workers.get(worker_id)
        if w and w.in_use > 0:
            w.in_use -= 1

    def broker_identity(self, token: str) -> str | None:
        """worker_id dono do token do broker ('*' = token compartilhado)."""
        import hmac
        for w in self.workers.values():
            if w.spec.broker_token and hmac.compare_digest(w.spec.broker_token.encode(),
                                                          token.encode()):
                return w.id
        for t in self.s.broker_tokens:
            if t and hmac.compare_digest(t.encode(), token.encode()):
                return "*"
        return None

    def snapshot(self) -> list[dict]:
        return [{"id": w.id, "healthy": w.healthy, "in_use": w.in_use, "slots": w.spec.slots,
                 "runs_total": w.runs_total, "reason": w.fail_reason} for w in self.workers.values()]
