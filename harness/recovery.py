"""Recuperacao apos reinicio do kernel (spec 5.3 "Falhas"; D-57).

As credenciais de uma run vivem so na memoria. Se o processo morreu, qualquer run que nao
chegou a um estado terminal NAO pode continuar e NUNCA e dada como concluida: vira
`interrupted` com o evento `error` (code=interrupted) para o cliente reenviar com nova chave.
"""
from __future__ import annotations

import logging

from harness.events import EventBus
from harness.store import TERMINAL_STATUSES, Store

log = logging.getLogger("harness.recovery")


async def recover_orphans(store: Store, bus: EventBus) -> list[str]:
    fixed: list[str] = []
    async for key in store.r.scan_iter(match="run:*", count=200):
        run_id = key.split(":", 1)[1]
        rec = await store.r.hgetall(key)
        if not rec or rec.get("terminal") or rec.get("status") in TERMINAL_STATUSES:
            continue
        if await store.mark_terminal(run_id, "interrupted", error_code="kernel_restarted"):
            await bus.error(run_id, "interrupted")
            fixed.append(run_id)
    if fixed:
        log.warning("recuperacao: %d run(s) em voo marcadas como interrupted", len(fixed))
    return fixed
