"""Fila e agendamento (spec 5.3).

- Redis Streams `runs:queue` + consumer group `dispatch` (log duravel; XACK quando a run sai
  da fila). Um loop de intake faz XREADGROUP (primeiro o PEL do proprio consumidor, depois
  novas) e alimenta indices em memoria por empresa.
- Justica: fair queuing ponderado por empresa (tempo virtual += 1/peso a cada despacho),
  teto de runs simultaneas por empresa (padrao 2), prioridade (canal externo > chat interno).
- Ordem por chat: so a run mais antiga de cada chat e elegivel + lock Redis por chat
  (SET NX PX com renovacao).
- Posicao na fila (evento `step kind=queue`) e expiracao em 60 s.
- O corpo da run (com credenciais) fica so na memoria (`PendingRuns`): apos reinicio do
  kernel as runs enfileiradas viram `interrupted` (credenciais perdidas por desenho).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import redis.asyncio as aioredis

from harness.config import Settings
from harness.events import EventBus
from harness.runctx import wipe_obj
from harness.store import Store
from harness.workers import Worker, WorkerRegistry

log = logging.getLogger("harness.scheduler")

STREAM = "runs:queue"
GROUP = "dispatch"
PRIORITY = {"chatwoot": 0, "api": 0, "web": 1, "schedule": 2}  # menor = mais urgente


def priority_for(channel: str) -> int:
    return PRIORITY.get(channel, 1)


class Requeue(Exception):
    """O runner devolve a run a fila (worker sem slot / fora do ar antes de iniciar)."""

    def __init__(self, delay: float = 0.5, reason: str = ""):
        super().__init__(reason)
        self.delay = delay
        self.reason = reason


@dataclass
class QueuedRun:
    run_id: str
    tenant_id: str
    chat_id: str
    template_id: str
    priority: int
    enq_ts: float
    stream_id: str = ""
    last_position: int = 0
    cancelled: bool = False
    not_before: float = 0.0
    attempts: int = 0


@dataclass
class Grant:
    worker: Worker
    lock_token: str


class PendingRuns:
    """Corpos completos (com credenciais) aguardando despacho. So memoria."""

    def __init__(self) -> None:
        self._d: dict[str, dict] = {}

    def put(self, run_id: str, body: dict) -> None:
        self._d[run_id] = body

    def get(self, run_id: str) -> dict | None:
        return self._d.get(run_id)

    def pop(self, run_id: str) -> dict | None:
        return self._d.pop(run_id, None)

    def __len__(self) -> int:
        return len(self._d)


Runner = Callable[[QueuedRun, Grant, dict], Awaitable[None]]


@dataclass
class Scheduler:
    settings: Settings
    store: Store
    bus: EventBus
    workers: WorkerRegistry
    pending: PendingRuns
    runner: Runner
    consumer: str = "harness-1"
    queues: dict[str, list[QueuedRun]] = field(default_factory=dict)
    running: Counter = field(default_factory=Counter)
    running_chats: set = field(default_factory=set)
    vtime: dict[str, float] = field(default_factory=dict)
    tasks: set = field(default_factory=set)
    _wake: asyncio.Event = field(default_factory=asyncio.Event)
    _loops: list = field(default_factory=list)
    _known: set = field(default_factory=set)
    cancel_flags: set = field(default_factory=set)
    tenant_caps: dict[str, int] = field(default_factory=dict)

    # ------------------------------------------------------------ ciclo
    async def start(self) -> None:
        await self._ensure_group()
        self._loops = [asyncio.create_task(self._intake_loop()),
                       asyncio.create_task(self._tick_loop())]

    async def stop(self) -> None:
        for t in self._loops:
            t.cancel()
        for t in list(self.tasks):
            t.cancel()
        await asyncio.gather(*self._loops, *self.tasks, return_exceptions=True)

    def wake(self) -> None:
        self._wake.set()

    async def _ensure_group(self) -> None:
        try:
            await self.store.r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        except aioredis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    # ------------------------------------------------------------ submit
    def queued_total(self) -> int:
        return sum(len(v) for v in self.queues.values())

    def queued_for(self, tenant_id: str) -> int:
        return len(self.queues.get(tenant_id, []))

    def over_capacity(self, tenant_id: str) -> bool:
        return (self.queued_total() >= self.settings.queue_max_total
                or self.queued_for(tenant_id) >= self.settings.queue_max_per_tenant)

    async def submit(self, body: dict, priority: int) -> None:
        self.pending.put(body["run_id"], body)
        await self.store.r.xadd(STREAM, {
            "run_id": body["run_id"], "tenant_id": body["tenant_id"], "chat_id": body["chat_id"],
            "template_id": body["template"]["id"], "priority": priority, "ts": time.time()})

    # ------------------------------------------------------------ intake
    async def _intake_loop(self) -> None:
        backlog = True  # primeiro o PEL deste consumidor (restart do kernel/Redis)
        while True:
            try:
                res = await self.store.r.xreadgroup(
                    GROUP, self.consumer, {STREAM: "0" if backlog else ">"}, count=50,
                    block=None if backlog else 500)
                got = False
                for _s, entries in res or []:
                    for sid, f in entries:
                        got = True
                        await self._on_entry(sid, f)
                if backlog and not got:
                    backlog = False
                if got:
                    self.wake()
                else:
                    await asyncio.sleep(0.005)  # cede o loop (BLOCK pode retornar na hora)
            except asyncio.CancelledError:
                raise
            except aioredis.ResponseError as exc:
                if "NOGROUP" in str(exc):
                    log.warning("grupo da fila ausente (Redis sem estado): recriando")
                    await self._ensure_group()
                    await self._resubmit_from_memory()
                else:
                    log.exception("intake: erro Redis")
                    await asyncio.sleep(0.5)
            except (aioredis.ConnectionError, aioredis.TimeoutError, OSError):
                log.warning("intake: Redis indisponivel; tentando de novo")
                await asyncio.sleep(0.5)
            except Exception:  # noqa: BLE001
                log.exception("intake: erro inesperado")
                await asyncio.sleep(0.5)

    async def _resubmit_from_memory(self) -> None:
        """Redis voltou vazio: o que ainda esta na memoria deste processo reentra."""
        for rid, body in list(self.pending._d.items()):
            if rid in self._known:
                continue
            await self.store.r.xadd(STREAM, {
                "run_id": rid, "tenant_id": body["tenant_id"], "chat_id": body["chat_id"],
                "template_id": body["template"]["id"],
                "priority": priority_for(body.get("channel", "web")), "ts": time.time()})

    async def _on_entry(self, sid: str, f: dict) -> None:
        run_id = f["run_id"]
        if run_id in self._known:
            await self.store.r.xack(STREAM, GROUP, sid)
            return
        if self.pending.get(run_id) is None:
            # corpo (credenciais) perdido: processo reiniciou. Nunca finge sucesso.
            log.warning("run %s sem corpo em memoria apos restart -> interrupted", run_id)
            await self.store.r.xack(STREAM, GROUP, sid)
            if await self.store.mark_terminal(run_id, "interrupted", error_code="kernel_restarted"):
                await self.bus.error(run_id, "kernel_restarted")
            return
        self._known.add(run_id)
        q = QueuedRun(run_id, f["tenant_id"], f["chat_id"], f["template_id"],
                      int(f.get("priority", 1)), float(f.get("ts", time.time())), sid)
        if f["tenant_id"] not in self.queues or not self.queues[f["tenant_id"]]:
            # tenant entrando: parte do tempo virtual minimo (nao fura a fila nem fica para tras)
            active = [self.vtime[t] for t, v in self.queues.items() if v and t in self.vtime]
            running = [self.vtime[t] for t, n in self.running.items() if n and t in self.vtime]
            floor = min(active + running, default=0.0)
            self.vtime[f["tenant_id"]] = max(self.vtime.get(f["tenant_id"], 0.0), floor)
        self.queues.setdefault(f["tenant_id"], []).append(q)

    # -------------------------------------------------------------- tick
    async def _tick_loop(self) -> None:
        while True:
            try:
                try:
                    await asyncio.wait_for(self._wake.wait(), self.settings.scheduler_tick_s)
                except TimeoutError:
                    pass
                self._wake.clear()
                await self.tick()
            except asyncio.CancelledError:
                raise
            except (aioredis.ConnectionError, aioredis.TimeoutError, OSError):
                await asyncio.sleep(0.3)
            except Exception:  # noqa: BLE001
                log.exception("tick falhou")
                await asyncio.sleep(0.2)

    def tenant_cap(self, tenant_id: str) -> int:
        return self.tenant_caps.get(tenant_id, self.settings.tenant_concurrency)

    def _weight(self, tenant_id: str) -> float:
        return float(self.settings.tenant_weights.get(tenant_id, 1.0)) or 1.0

    async def tick(self) -> None:
        now = time.time()
        await self._expire(now)
        launched_any = True
        blocked: set[str] = set()
        while launched_any:
            launched_any = False
            for q in self._candidates(blocked):
                if await self._try_launch(q):
                    launched_any = True
                    break
                blocked.add(q.run_id)
        await self._announce_positions()

    def _candidates(self, blocked: set[str]) -> list[QueuedRun]:
        """Runs elegiveis ordenadas por (prioridade, tempo virtual do tenant, chegada)."""
        out: list[QueuedRun] = []
        for tenant, lst in self.queues.items():
            if self.running[tenant] >= self.tenant_cap(tenant):
                continue
            seen_chats: set[str] = set()
            for q in lst:  # lst em ordem de chegada
                if q.chat_id in seen_chats:
                    continue  # ordem por chat: so a mais antiga de cada chat
                seen_chats.add(q.chat_id)
                if (tenant, q.chat_id) in self.running_chats or q.run_id in blocked:
                    continue
                if q.not_before > time.time():
                    continue
                out.append(q)
        out.sort(key=lambda q: (q.priority, self.vtime.get(q.tenant_id, 0.0), q.enq_ts))
        return out

    async def _try_launch(self, q: QueuedRun) -> bool:
        worker = await self.workers.assign(q.tenant_id)
        if worker is None:
            return False
        if not self.workers.try_acquire(worker):
            return False
        token = await self.store.acquire_chat_lock(q.tenant_id, q.chat_id)
        if token is None:
            self.workers.release(worker.id)
            return False
        lst = self.queues[q.tenant_id]
        lst.remove(q)
        if not lst:
            self.queues.pop(q.tenant_id, None)
        self.running[q.tenant_id] += 1
        self.running_chats.add((q.tenant_id, q.chat_id))
        self.vtime[q.tenant_id] = self.vtime.get(q.tenant_id, 0.0) + 1.0 / self._weight(q.tenant_id)
        body = self.pending.get(q.run_id) or {}
        t = asyncio.create_task(self._run(q, Grant(worker, token), body))
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)
        return True

    async def _run(self, q: QueuedRun, grant: Grant, body: dict) -> None:
        renew = asyncio.create_task(self._renew_lock(q, grant.lock_token))
        requeue: Requeue | None = None
        try:
            try:
                await self.store.r.xack(STREAM, GROUP, q.stream_id)
            except Exception:  # noqa: BLE001
                log.warning("xack falhou run=%s", q.run_id)
            await self.runner(q, grant, body)
        except asyncio.CancelledError:
            raise
        except Requeue as rq:
            requeue = rq
        except Exception:  # noqa: BLE001
            log.exception("runner falhou run=%s", q.run_id)
        finally:
            renew.cancel()
            try:
                await self.store.release_chat_lock(q.tenant_id, q.chat_id, grant.lock_token)
            except Exception:  # noqa: BLE001
                log.warning("release do lock falhou run=%s", q.run_id)
            self.workers.release(grant.worker.id)
            self.running[q.tenant_id] -= 1
            if self.running[q.tenant_id] <= 0:
                self.running.pop(q.tenant_id, None)
            self.running_chats.discard((q.tenant_id, q.chat_id))
            if requeue is not None:
                await self._requeue(q, requeue)
            else:
                wipe_obj(self.pending.pop(q.run_id))  # credenciais: esquecidas ao fim da run
                self._known.discard(q.run_id)
            self.wake()

    async def _requeue(self, q: QueuedRun, rq: Requeue) -> None:
        """429 / worker fora antes de iniciar: volta para a fila na posicao de chegada."""
        log.info("run %s devolvida a fila (%s)", q.run_id, rq.reason)
        q.attempts += 1
        q.not_before = time.time() + rq.delay * min(q.attempts, 5)
        q.last_position = 0
        lst = self.queues.setdefault(q.tenant_id, [])
        lst.append(q)
        lst.sort(key=lambda x: x.enq_ts)
        try:
            await self.store.set_run(q.run_id, status="queued", requeues=q.attempts)
        except Exception:  # noqa: BLE001
            pass

    async def _renew_lock(self, q: QueuedRun, token: str) -> None:
        interval = self.settings.chat_lock_ttl_ms / 3000.0
        while True:
            await asyncio.sleep(interval)
            try:
                r = await self.store.renew_chat_lock(q.tenant_id, q.chat_id, token)
                if r == -1:
                    log.error("lock do chat perdido run=%s", q.run_id)
            except (aioredis.ConnectionError, aioredis.TimeoutError, OSError):
                continue

    # ------------------------------------------------- expiracao / posicao
    async def _expire(self, now: float) -> None:
        limit = self.settings.queue_expiry_s
        for tenant, lst in list(self.queues.items()):
            for q in list(lst):
                if now - q.enq_ts >= limit:
                    lst.remove(q)
                    await self._finish_queued(q, "expired", "queue_expired")
            if not lst:
                self.queues.pop(tenant, None)

    async def _finish_queued(self, q: QueuedRun, status: str, code: str) -> None:
        try:
            await self.store.r.xack(STREAM, GROUP, q.stream_id)
        except Exception:  # noqa: BLE001
            pass
        if await self.store.mark_terminal(q.run_id, status, error_code=code):
            await self.bus.error(q.run_id, code)
        wipe_obj(self.pending.pop(q.run_id))
        self._known.discard(q.run_id)

    async def cancel_queued(self, run_id: str) -> bool:
        for tenant, lst in list(self.queues.items()):
            for q in lst:
                if q.run_id == run_id:
                    lst.remove(q)
                    if not lst:
                        self.queues.pop(tenant, None)
                    await self._finish_queued(q, "cancelled", "cancelled")
                    return True
        return False

    def eta_s(self, position: int) -> int:
        """Estimativa grosseira: runs a frente / slots livres do pool x duracao media."""
        slots = max(1, sum(w.spec.slots for w in self.workers.workers.values() if w.healthy))
        return int(round(position / slots * self.settings.run_est_s)) + 1

    def ordered_queue(self) -> list[QueuedRun]:
        allq = [q for lst in self.queues.values() for q in lst]
        allq.sort(key=lambda q: (q.priority, q.enq_ts))
        return allq

    async def _announce_positions(self) -> None:
        for pos, q in enumerate(self.ordered_queue(), start=1):
            if q.last_position != pos:
                q.last_position = pos
                eta = self.eta_s(pos)
                await self.bus.publish(q.run_id, "step", {
                    "kind": "queue", "label": f"Na fila (posição {pos})",
                    "status": "start", "position": pos, "eta_s": eta})

    def stats(self) -> dict:
        return {"queued": self.queued_total(), "running": sum(self.running.values()),
                "pending_bodies": len(self.pending),
                "queued_by_tenant": {t: len(v) for t, v in self.queues.items()},
                "running_by_tenant": dict(self.running)}
