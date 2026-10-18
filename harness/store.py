"""Estado duravel no Redis: runs, idempotencia, stream de eventos, locks, afinidade, usage.

Nada aqui recebe credenciais: o corpo guardado para a fila e o pedido SEM `credentials` e
SEM `run_token`.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any

import redis.asyncio as aioredis

from harness.config import Settings

TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted", "expired"}

_RELEASE_LOCK = """
if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end
"""
_RENEW_LOCK = """
local v = redis.call('get', KEYS[1])
if v == ARGV[1] then return redis.call('pexpire', KEYS[1], ARGV[2]) end
if v == false then return redis.call('set', KEYS[1], ARGV[1], 'NX', 'PX', ARGV[2]) and 1 or 0 end
return -1
"""


def canonical_hash(body: dict) -> str:
    clean = {k: v for k, v in body.items() if k != "run_token"}
    raw = json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def strip_secrets(body: dict) -> dict:
    """Copia do pedido que PODE ir para o Redis (sem credenciais nem token)."""
    return {k: v for k, v in body.items() if k not in ("credentials", "run_token")}


class Store:
    def __init__(self, redis: aioredis.Redis, settings: Settings):
        self.r = redis
        self.s = settings
        self._release = self.r.register_script(_RELEASE_LOCK)
        self._renew = self.r.register_script(_RENEW_LOCK)

    # ------------------------------------------------------------ runs
    async def create_run(self, body: dict, priority: int) -> bool:
        key = f"run:{body['run_id']}"
        created = await self.r.hsetnx(key, "run_id", body["run_id"])
        if not created:
            return False
        await self.r.hset(key, mapping={
            "tenant_id": body["tenant_id"], "user_id": body.get("user_id") or "",
            "chat_id": body["chat_id"], "template_id": body["template"]["id"],
            "channel": body.get("channel", "web"), "priority": priority,
            "status": "queued", "enq_ts": time.time(), "cancel": 0,
        })
        await self.r.expire(key, self.s.run_state_ttl_s)
        await self.r.set(f"runbody:{body['run_id']}", json.dumps(strip_secrets(body)),
                         ex=self.s.run_state_ttl_s)
        return True

    async def get_run(self, run_id: str) -> dict | None:
        data = await self.r.hgetall(f"run:{run_id}")
        return data or None

    async def set_run(self, run_id: str, **fields: Any) -> None:
        await self.r.hset(f"run:{run_id}", mapping={k: str(v) for k, v in fields.items()})

    async def mark_terminal(self, run_id: str, status: str, **fields: Any) -> bool:
        """Transicao unica para estado terminal (protege contra terminal duplo)."""
        key = f"run:{run_id}"
        won = await self.r.hsetnx(key, "terminal", status)
        if not won:
            return False
        await self.r.hset(key, mapping={"status": status, "end_ts": time.time(),
                                        **{k: str(v) for k, v in fields.items()}})
        return True

    async def claim_idempotency(self, tenant_id: str, key: str, body_hash: str,
                                run_id: str) -> tuple[str, str]:
        """('new'|'replay'|'conflict', run_id existente)."""
        k = f"idem:{tenant_id}:{key}"
        value = json.dumps({"hash": body_hash, "run_id": run_id})
        if await self.r.set(k, value, nx=True, ex=self.s.idempotency_ttl_s):
            return "new", run_id
        cur = json.loads(await self.r.get(k) or "{}")
        if not cur:  # expirou entre o SET e o GET
            return await self.claim_idempotency(tenant_id, key, body_hash, run_id)
        if cur.get("hash") == body_hash:
            return "replay", cur["run_id"]
        return "conflict", cur["run_id"]

    async def release_idempotency(self, tenant_id: str, key: str) -> None:
        await self.r.delete(f"idem:{tenant_id}:{key}")

    # ---------------------------------------------------------- eventos
    async def add_event(self, run_id: str, etype: str, data: dict) -> str:
        key = f"events:{run_id}"
        eid = await self.r.xadd(key, {"type": etype, "data": json.dumps(data, ensure_ascii=False)},
                                maxlen=5000, approximate=True)
        await self.r.expire(key, self.s.events_ttl_s)
        return eid

    async def read_events(self, run_id: str, last_id: str, block_ms: int = 1000,
                          count: int = 100) -> list[tuple[str, str, dict]]:
        res = await self.r.xread({f"events:{run_id}": last_id}, count=count,
                                 block=block_ms if block_ms else None)
        out = []
        for _stream, entries in res or []:
            for eid, fields in entries:
                out.append((eid, fields["type"], json.loads(fields["data"])))
        return out

    async def events_exist(self, run_id: str) -> bool:
        return bool(await self.r.exists(f"events:{run_id}"))

    # ------------------------------------------------------------ locks
    @staticmethod
    def chat_lock_key(tenant_id: str, chat_id: str) -> str:
        return f"lock:chat:{tenant_id}:{chat_id}"

    async def acquire_chat_lock(self, tenant_id: str, chat_id: str) -> str | None:
        token = uuid.uuid4().hex
        ok = await self.r.set(self.chat_lock_key(tenant_id, chat_id), token, nx=True,
                              px=self.s.chat_lock_ttl_ms)
        return token if ok else None

    async def renew_chat_lock(self, tenant_id: str, chat_id: str, token: str) -> int:
        return int(await self._renew(keys=[self.chat_lock_key(tenant_id, chat_id)],
                                     args=[token, self.s.chat_lock_ttl_ms]))

    async def release_chat_lock(self, tenant_id: str, chat_id: str, token: str) -> None:
        await self._release(keys=[self.chat_lock_key(tenant_id, chat_id)], args=[token])

    # --------------------------------------------------------- afinidade
    async def get_affinity(self, tenant_id: str) -> str | None:
        return await self.r.hget("affinity", tenant_id)

    async def set_affinity(self, tenant_id: str, worker_id: str) -> None:
        await self.r.hset("affinity", tenant_id, worker_id)

    # ------------------------------------------------------------- usage
    async def get_usage_prev(self, session_id: str) -> dict:
        raw = await self.r.get(f"usage_prev:{session_id}")
        return json.loads(raw) if raw else {}

    async def set_usage_prev(self, session_id: str, usage: dict) -> None:
        await self.r.set(f"usage_prev:{session_id}", json.dumps(usage), ex=30 * 86_400)

    # ----------------------------------------------------------- approvals
    async def save_approval(self, approval_id: str, fields: dict) -> None:
        await self.r.hset(f"approval:{approval_id}", mapping={k: str(v) for k, v in fields.items()})
        await self.r.expire(f"approval:{approval_id}", 3600)

    async def get_approval(self, approval_id: str) -> dict | None:
        return (await self.r.hgetall(f"approval:{approval_id}")) or None

    async def decide_approval(self, approval_id: str, status: str) -> bool:
        """Decisao unica: so a primeira vale."""
        key = f"approval:{approval_id}"
        if not await self.r.hsetnx(key, "decided", status):
            return False
        await self.r.hset(key, "status", status)
        return True
