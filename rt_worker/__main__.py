"""Entrypoint do container: sobe o zigoto (processo quente) e o front HTTP; reinicia o zigoto se cair."""
from __future__ import annotations

import asyncio
import logging
import os
import sys

log = logging.getLogger("rt_worker")


async def _keep_zygote(sock: str) -> None:
    py = os.environ.get("HERMES_PYTHON", "/opt/hermes/.venv/bin/python")
    while True:
        proc = await asyncio.create_subprocess_exec(py, "-m", "rt_worker.zygote", sock)
        rc = await proc.wait()
        log.error("zigoto saiu (rc=%s); reiniciando em 2s", rc)
        try:  # sem socket o /health do front fica 503 (warming) ate o novo zigoto aquecer
            os.unlink(sock)
        except OSError:
            pass
        await asyncio.sleep(2)


async def main() -> None:
    from rt_worker.front import Config, amain
    sock = Config.from_env().sock
    z = asyncio.create_task(_keep_zygote(sock))
    try:
        await amain()
    finally:
        z.cancel()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    asyncio.run(main())
