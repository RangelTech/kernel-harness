#!/usr/bin/env python3
"""Corta o consumo OCIOSO do gateway Hermes multiplexado (rt-hermes).

Com `gateway.multiplex_profiles` um unico processo serve TODOS os perfis (~200 por worker) e varios
pollers do upstream percorrem todos os perfis a cada tick, mesmo sem trabalho:

  * ticker de cron (60 s): `cron_tick` + 2 gravacoes com fsync (heartbeat) por perfil;
  * housekeeping "MCP config reconcile" (60 s): `discover_plugins()` (le 55 manifestos YAML) por perfil;
  * `_handoff_watcher` (2 s) e `_loop_wakeup_watcher` (15 s): abrem o state.db de cada perfil (numa thread
    nova) a cada volta;
  * varredura de `async completions` orfas (30 s): abre o state.db de cada perfil;
  * `trim_memory` (60 s): `gc.collect()` num heap de GBs, segurando o GIL (atrasa o loop e o /health).

O produto nao usa nada disso (cronjob/delegation desligados no perfil, sem MCP, sem handoff de CLI),
entao cada ponto ganha uma variavel de ambiente. O padrao do codigo continua sendo o do upstream:
quem nao exporta a variavel nao muda de comportamento. A imagem rt-hermes exporta os valores baixos
(ver docker/hermes.Dockerfile).

Uso:  python hermes_idle_cpu.py /opt/hermes
Falha (exit 1) se qualquer ancora nao for encontrada exatamente uma vez: o digest do Hermes e fixado, e um
upgrade que mude o texto tem de revisar este arquivo (e rodar tests/test_hermes_idle_patch.py).
"""
from __future__ import annotations

import py_compile
import sys
from pathlib import Path

# (arquivo relativo, trecho original, trecho novo)
PATCHES: list[tuple[str, str, str]] = [
    (
        "cron/scheduler_provider.py",
        '        logger.info("In-process cron scheduler started (interval=%ds)", interval)\n',
        '        logger.info("In-process cron scheduler started (interval=%ds)", interval)\n'
        "        import os as _rt_os\n"
        '        if _rt_os.environ.get("RT_HERMES_CRON", "1") == "0":\n'
        '            logger.info("In-process cron scheduler disabled (RT_HERMES_CRON=0)")\n'
        "            return\n",
    ),
    (
        "gateway/run.py",
        "    # Between ticks the queue file is watched so a worker's send goes out when it is queued,\n",
        "    # rt-hermes: chores que varrem todos os perfis a cada tick e nao servem ao produto.\n"
        "    import os as _rt_os\n"
        '    _rt_cron_off = _rt_os.environ.get("RT_HERMES_CRON", "1") == "0"\n'
        "    _rt_drop: set = set()\n"
        "    if _rt_cron_off:\n"
        '        _rt_drop.update({DRAIN_LABEL, "Misfire catch-up sweep", "Cron ticker supervisor"})\n'
        '    if _rt_os.environ.get("RT_HERMES_MCP_RECONCILE", "1") == "0":\n'
        '        _rt_drop.add("MCP config reconcile")\n'
        "    chores = [c for c in chores if c[1] not in _rt_drop]\n"
        "\n"
        "    # Between ticks the queue file is watched so a worker's send goes out when it is queued,\n",
    ),
    (
        "gateway/run.py",
        "    queue_watch = None\n    if adapters is not None or runner is not None:\n        def served_homes() -> list:\n",
        "    queue_watch = None\n"
        "    if (adapters is not None or runner is not None) and not _rt_cron_off:\n"
        "        def served_homes() -> list:\n",
    ),
    (
        "gateway/run.py",
        '    trim_memory(reason="messaging gateway housekeeping")\n',
        "    import os as _rt_os\n"
        '    trim_memory(reason="messaging gateway housekeeping",\n'
        '                cooldown_seconds=float(_rt_os.environ.get("RT_HERMES_MEM_TRIM_COOLDOWN_S", "60")))\n',
    ),
    (
        "gateway/run_adapters.py",
        "    async def _handoff_watcher(self, interval: float = 2.0, drain_timeout: float = 30.0) -> None:\n",
        "    async def _handoff_watcher(\n"
        "        self,\n"
        '        interval: float = float(__import__("os").environ.get("RT_HERMES_HANDOFF_POLL_S", "2.0")),\n'
        "        drain_timeout: float = 30.0,\n"
        "    ) -> None:\n",
    ),
    (
        "gateway/run_goals.py",
        "    async def _loop_wakeup_watcher(self, interval: float = 15.0) -> None:\n",
        "    async def _loop_wakeup_watcher(\n"
        '        self, interval: float = float(__import__("os").environ.get("RT_HERMES_LOOP_POLL_S", "15"))\n'
        "    ) -> None:\n",
    ),
    (
        "tools/async_delegation.py",
        "ORPHAN_SWEEP_INTERVAL_S = 30.0\n",
        'ORPHAN_SWEEP_INTERVAL_S = float(__import__("os").environ.get("RT_HERMES_ORPHAN_SWEEP_S", "30"))\n',
    ),
]


def apply(root: Path, patches=PATCHES) -> list[Path]:
    """Aplica todos os patches em `root`; nada e gravado se alguma ancora faltar ou repetir."""
    staged: dict[Path, str] = {}
    for rel, old, new in patches:
        path = root / rel
        text = staged.get(path)
        if text is None:
            text = path.read_text(encoding="utf-8")
        n = text.count(old)
        if n != 1:
            raise SystemExit(f"ancora de {rel} encontrada {n}x (esperado 1): {old.strip()[:80]!r}")
        staged[path] = text.replace(old, new, 1)
    for path, text in staged.items():
        path.write_text(text, encoding="utf-8")
    return list(staged)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    changed = apply(Path(argv[1]))
    for path in changed:
        py_compile.compile(str(path), doraise=True)  # sintaxe + pycache novo
        print("patched", path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
