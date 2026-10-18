"""Cliente da API interna do backend (agent-platform). O harness NAO fala com o Postgres.

Endpoints (spec 8.2 + api-contract.md; os marcados * sao assumidos aqui e listados como
itens abertos para o backend confirmar):
  POST /internal/runs/{run_id}/usage | messages | skills
  POST /internal/runs/{run_id}/memory-proposals *
  POST /internal/runs/{run_id}/artifacts/register-init | register-complete *
  POST /internal/runs/{run_id}/payments *            (registro de cobranca Pix)
  GET  /internal/payments/charges?...  *             (consulta por referencia)
  GET  /internal/skills/user/{tenant_id}/{user_id}
  POST /internal/approvals
  POST /internal/search/docs *                       (busca lexical)
  POST /internal/memories/recall *
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

import httpx

from harness.config import Settings

log = logging.getLogger("harness.backend")


class BackendError(Exception):
    def __init__(self, status: int, detail: str = ""):
        super().__init__(f"backend http {status}: {detail[:200]}")
        self.status = status


class Backend(Protocol):
    async def post_usage(self, run_id: str, token: str, payload: dict) -> None: ...
    async def post_message(self, run_id: str, token: str, payload: dict) -> None: ...
    async def post_skill_proposal(self, run_id: str, token: str, payload: dict) -> dict: ...
    async def post_memory_proposal(self, run_id: str, token: str, payload: dict) -> dict: ...
    async def get_user_skills(self, tenant_id: str, user_id: str) -> list[dict]: ...
    async def create_approval(self, run_id: str, token: str, payload: dict) -> str: ...
    async def artifact_put(self, run_id: str, token: str, payload: dict) -> dict: ...
    async def record_charge(self, run_id: str, token: str, payload: dict) -> None: ...
    async def lookup_charge(self, run_id: str, token: str, reference_id: str) -> dict | None: ...
    async def search_docs(self, run_id: str, token: str, payload: dict) -> list[dict]: ...
    async def recall_memories(self, run_id: str, token: str, payload: dict) -> list[dict]: ...
    async def fetch_package(self, url: str) -> dict: ...
    async def schedule_op(self, run_id: str, token: str, payload: dict) -> dict: ...
    async def aclose(self) -> None: ...


class HttpBackend:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None):
        self.s = settings
        self.http = http or httpx.AsyncClient(timeout=20.0)
        self._own = http is None

    async def aclose(self) -> None:
        if self._own:
            await self.http.aclose()

    def _h(self, run_token: str | None = None) -> dict:
        h = {}
        if self.s.backend_internal_token:
            h["X-Internal-Token"] = self.s.backend_internal_token
        h["Authorization"] = f"Bearer {run_token or self.s.backend_internal_token}"
        return h

    async def _post(self, path: str, token: str | None, payload: dict, *, timeout: float = 20.0
                    ) -> Any:
        r = await self.http.post(f"{self.s.backend_url}{path}", json=payload,
                                 headers=self._h(token), timeout=timeout)
        if r.status_code >= 400:
            raise BackendError(r.status_code, r.text)
        return r.json() if r.content else {}

    async def post_usage(self, run_id, token, payload):
        await self._post(f"/internal/runs/{run_id}/usage", token, payload)

    async def post_message(self, run_id, token, payload):
        await self._post(f"/internal/runs/{run_id}/messages", token, payload)

    async def post_skill_proposal(self, run_id, token, payload):
        return await self._post(f"/internal/runs/{run_id}/skills", token, payload)

    async def post_memory_proposal(self, run_id, token, payload):
        return await self._post(f"/internal/runs/{run_id}/memory-proposals", token, payload)

    async def get_user_skills(self, tenant_id, user_id):
        r = await self.http.get(f"{self.s.backend_url}/internal/skills/user/{tenant_id}/{user_id}",
                                headers=self._h(), timeout=15.0)
        if r.status_code >= 400:
            raise BackendError(r.status_code, r.text)
        data = r.json()
        return data["skills"] if isinstance(data, dict) else data

    async def create_approval(self, run_id, token, payload):
        data = await self._post("/internal/approvals", token, payload)
        return data["approval_id"]

    async def artifact_put(self, run_id, token, payload):
        """O backend guarda o arquivo (o kernel nao tem credencial de armazenamento) e registra."""
        return await self._post(f"/internal/runs/{run_id}/artifacts/put", token, payload,
                                timeout=90.0)

    async def record_charge(self, run_id, token, payload):
        await self._post(f"/internal/runs/{run_id}/payments", token, payload)

    async def lookup_charge(self, run_id, token, reference_id):
        r = await self.http.get(f"{self.s.backend_url}/internal/payments/charges",
                                params={"reference_id": reference_id, "run_id": run_id},
                                headers=self._h(token), timeout=15.0)
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise BackendError(r.status_code, r.text)
        return r.json()

    async def schedule_op(self, run_id, token, payload):
        return await self._post(f"/internal/runs/{run_id}/schedules", token, payload)

    async def search_docs(self, run_id, token, payload):
        data = await self._post("/internal/search/docs", token, payload)
        return data["results"] if isinstance(data, dict) else data

    async def recall_memories(self, run_id, token, payload):
        data = await self._post("/internal/memories/recall", token, payload)
        return data["memories"] if isinstance(data, dict) else data

    async def fetch_package(self, url: str) -> dict:
        r = await self.http.get(url, timeout=30.0)
        if r.status_code >= 400:
            raise BackendError(r.status_code, "pacote")
        return r.json()
