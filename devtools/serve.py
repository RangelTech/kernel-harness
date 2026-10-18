"""Sobe um app ASGI com uvicorn REAL (socket local) dentro do loop atual.

Necessario porque `httpx.ASGITransport` nao faz streaming incremental (SSE).
"""
from __future__ import annotations

import asyncio

import uvicorn


class Served:
    def __init__(self, app, host: str = "127.0.0.1", port: int = 0, lifespan: str = "off"):
        self.app = app
        self.host = host
        self.port = port
        self.lifespan = lifespan
        self.server: uvicorn.Server | None = None
        self.task: asyncio.Task | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    async def start(self) -> Served:
        cfg = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="warning",
                             lifespan=self.lifespan, access_log=False)
        self.server = uvicorn.Server(cfg)
        self.task = asyncio.create_task(self.server.serve())
        for _ in range(400):
            if self.server.started:
                break
            if self.task.done():
                self.task.result()
            await asyncio.sleep(0.01)
        else:
            raise RuntimeError("uvicorn nao subiu")
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        if self.server is None or self.task is None:
            return
        self.server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(self.task), 5)
        except TimeoutError:
            self.server.force_exit = True
            try:
                await asyncio.wait_for(self.task, 5)
            except (TimeoutError, asyncio.CancelledError):
                self.task.cancel()
        self.server = self.task = None

    async def kill(self) -> None:
        """Como um kill -9: derruba as conexoes abertas e fecha a porta, sem terminal event."""
        if self.server is None:
            return
        for conn in list(self.server.server_state.connections):
            tr = getattr(conn, "transport", None)
            if tr is not None:
                tr.abort()
        await self.stop()
