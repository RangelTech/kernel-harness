"""Rig de testes OFFLINE: fakeredis + Hermes falso (uvicorn real em 127.0.0.1) + backend falso
+ externos falsos (MockTransport) + a propria API do kernel (uvicorn real, para testar SSE)."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import fakeredis
import httpx
import pytest

from devtools.fake_backend import FakeBackend
from devtools.fake_externals import FakeExternals
from devtools.fake_hermes import FakeHermes
from devtools.serve import Served
from harness.api import create_app
from harness.config import Settings, WorkerSpec
from harness.kernel import Kernel
from harness.security import sign_run_token
from harness.workers import NoopFencer

SERVICE_TOKEN = "svc-token-test-0001"
RUN_SECRET = "run-token-secret-test"
BROKER_TOKEN = "broker-token-test-0001"

# credenciais sentinela: NENHUMA pode aparecer em SSE, logs, Redis, perfil, pending/ ...
S_APIKEY = "SENTINEL-APIKEY-7f3a9c21"
S_MP = "SENTINEL-MPTOKEN-5512345"
S_MAIL = "SENTINEL-MAILPASS-99812"
S_DB = "SENTINEL-DBPASS-44417"
SENTINELS = [S_APIKEY, S_MP, S_MAIL, S_DB]

ALL_TOOLS = ["rt_echo", "rt_data_describe", "rt_sql_read", "rt_sql_write", "rt_sql_transaction",
             "rt_pix_create", "rt_payment_status", "rt_mp_read", "rt_email_list",
             "rt_email_send", "rt_http", "rt_search_docs", "rt_memory_recall", "rt_artifact_put",
             "rt_executor_run", "rt_env_status", "rt_env_start", "rt_env_stop", "rt_env_backup",
             "rt_env_expose", "rt_env_unexpose", "rt_env_domain_add", "rt_env_domain_verify",
             "rt_schedule_create", "rt_schedule_list", "rt_schedule_pause", "rt_schedule_delete"]


@dataclass
class Ev:
    id: str
    type: str
    data: dict


def make_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE clientes (id INTEGER PRIMARY KEY, nome TEXT, saldo REAL);
        CREATE TABLE pedidos (id INTEGER PRIMARY KEY AUTOINCREMENT, cliente_id INTEGER, valor REAL);
        INSERT INTO clientes VALUES (1,'Ana',10.5),(2,'Bruno',20),(3,'Caio',30);
    """)
    con.commit()
    con.close()


def default_creds(db_path: str) -> dict:
    return {
        "secrets": {"API_KEY": S_APIKEY},
        "datasources": [{"name": "vendas", "kind": "sqlite", "config": {"path": db_path},
                         "secret": S_DB}],
        "payment": {"provider": "mercado_pago", "access_token": S_MP, "sandbox": True,
                    "notification_url": "https://hook.test/mp"},
        "email_accounts": [{"label": "Financeiro", "email_address": "fin@empresa.test",
                            "imap_host": "imap.test", "imap_port": 993, "username": "fin",
                            "password": S_MAIL, "smtp_host": "smtp.test", "smtp_port": 587,
                            "use_tls": True}],
    }


class FakeEmail:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def list_inbox(self, account, folder, limit):
        return [{"from": "x@y.test", "subject": "Oi", "date": "hoje", "snippet": "corpo"}]

    async def send(self, account, to, subject, body):
        self.sent.append({"account": account["label"], "to": to, "subject": subject, "body": body})


@dataclass
class Rig:
    tmp: Path
    settings: Settings
    redis_server: fakeredis.FakeServer
    backend: FakeBackend
    ext: FakeExternals
    email: FakeEmail
    hermes: list[FakeHermes]
    hermes_served: list[Served]
    kernel: Kernel | None = None
    api: Served | None = None
    client: httpx.AsyncClient | None = None
    db_path: str = ""
    tasks: list = field(default_factory=list)

    # ------------------------------------------------------------ ciclo
    def new_redis(self):
        return fakeredis.FakeAsyncRedis(server=self.redis_server, decode_responses=True)

    async def start_kernel(self) -> None:
        self.kernel = Kernel(self.settings, redis=self.new_redis(), backend=self.backend,
                             http=self.ext.client(), fencer=NoopFencer(),
                             email_transport=self.email,
                             resolver=self._resolver)
        self.api = await Served(create_app(kernel=self.kernel), lifespan="on").start()
        self.client = httpx.AsyncClient(base_url=self.api.url, timeout=60.0)
        for h in self.hermes:
            h.broker_url = f"{self.api.url}/broker/v1/call"
            h.broker_token = BROKER_TOKEN

    @staticmethod
    async def _resolver(host: str) -> list[str]:
        return {"api.partner.test": ["93.184.216.34"], "internal.test": ["10.0.0.5"],
                "meta.test": ["169.254.169.254"], "local.test": ["127.0.0.1"]}.get(
            host, ["93.184.216.34"])

    async def stop_kernel(self) -> None:
        if self.client:
            await self.client.aclose()
        if self.api:
            await self.api.stop()
        self.client = self.api = self.kernel = None

    async def restart_kernel(self) -> None:
        await self.stop_kernel()
        await self.start_kernel()

    # ------------------------------------------------------------- corpos
    def body(self, *, tenant="t1", user: str | None = "u1", chat="c1", message="oi",
             template="tpl1", version="v1", run_id=None, tools=None, creds="default",
             limits=None, channel="web", actor_type="member", extra=None,
             grants=None, files=None) -> dict:
        run_id = run_id or "r" + uuid.uuid4().hex[:10]
        manifest = {"tools": tools if tools is not None else list(ALL_TOOLS),
                    "limits": {"write_tables": ["pedidos"], "http_allowed_hosts":
                               ["api.partner.test", "internal.test", "meta.test", "local.test"],
                               **(limits or {})}}
        if grants is not None:
            manifest["credential_grants"] = [{"kind": k} for k in grants]
        pkg_files = files if files is not None else [
            {"path": "AGENTE.md", "content": "Voce e o agente de teste."},
            {"path": "skills/triagem/SKILL.md",
             "content": "---\nname: triagem\ndescription: Triagem de chamados\n---\nCorpo.\n"}]
        body = {
            "contract_version": 1, "run_id": run_id, "idempotency_key": "idem-" + run_id,
            "tenant_id": tenant, "user_id": user, "actor": {"type": actor_type, "name": "Ana"},
            "chat_id": chat, "channel": channel, "message_id": "m-" + run_id,
            "template": {"id": template, "version_id": version, "kind": "skills"},
            "message": message, "attachments": [], "history": [],
            "model": {"alias": "agente-padrao", "reasoning_effort": "medium"},
            "skills": {"system_rev": "s1",
                       "tenant_package": {"sha256": "pkg-" + version, "files": pkg_files},
                       "user_skills": []},
            "manifest": manifest,
            "credentials": default_creds(self.db_path) if creds == "default" else (creds or {}),
            "credentials_catalog": [{"kind": "mercado_pago", "label": "Loja (sandbox)"}],
            "memory": {"recall": []},
        }
        body.update(extra or {})
        body["run_token"] = sign_run_token(
            {"run_id": run_id, "tenant_id": tenant, "user_id": user,
             "exp": time.time() + 600}, RUN_SECRET)
        return body

    # ---------------------------------------------------------------- SSE
    async def send(self, body: dict, *, token: str | None = SERVICE_TOKEN,
                   on_event: Callable[[Ev], Awaitable[None]] | None = None,
                   headers: dict | None = None, path: str = "/v1/runs",
                   method: str = "POST") -> list[Ev]:
        """Envia e le o SSE ate o fim. Em erro HTTP devolve [Ev('http', status, json)]."""
        h = dict(headers or {})
        if token:
            h["Authorization"] = f"Bearer {token}"
        assert self.client is not None
        out: list[Ev] = []
        async with self.client.stream(method, path, json=body if method == "POST" else None,
                                      headers=h) as r:
            if r.status_code != 200 or "text/event-stream" not in r.headers.get(
                    "content-type", ""):
                raw = (await r.aread()).decode()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    data = {"raw": raw}
                return [Ev("", "http", {"status": r.status_code, **data})]
            cur_id, cur_ev, cur_data = "", "", []
            async for line in r.aiter_lines():
                if line == "":
                    if cur_data:
                        ev = Ev(cur_id, cur_ev, json.loads("\n".join(cur_data)))
                        out.append(ev)
                        if on_event:
                            await on_event(ev)
                    cur_id, cur_ev, cur_data = "", "", []
                elif line.startswith("id:"):
                    cur_id = line[3:].strip()
                elif line.startswith("event:"):
                    cur_ev = line[6:].strip()
                elif line.startswith("data:"):
                    cur_data.append(line[5:].lstrip())
        return out

    def spawn(self, body: dict, **kw) -> asyncio.Task:
        t = asyncio.create_task(self.send(body, **kw))
        self.tasks.append(t)
        return t

    async def decide(self, approval_id: str, decision: str, tenant="t1", user="u1"):
        assert self.client is not None
        r = await self.client.post(f"/v1/approvals/{approval_id}", json={
            "decision": decision, "tenant_id": tenant, "user_id": user},
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"})
        return r

    def approver(self, decision: str = "approve", tenant="t1", user="u1"):
        async def _on(ev: Ev) -> None:
            if ev.type == "approval_required":
                await self.decide(ev.data["approval_id"], decision, tenant, user)
        return _on

    async def broker(self, session_id: str, tool: str, args: dict, token: str = BROKER_TOKEN):
        assert self.client is not None
        r = await self.client.post("/broker/v1/call", json={
            "session_id": session_id, "tool": tool, "args": args},
            headers={"Authorization": f"Bearer {token}"})
        return r.status_code, r.json()


def types(events: list[Ev]) -> list[str]:
    return [e.type for e in events]


def final(events: list[Ev]) -> Ev:
    return events[-1]


def text_of(events: list[Ev]) -> str:
    return "".join(e.data["text"] for e in events if e.type == "token")


@pytest.fixture
async def make_rig(tmp_path):
    rigs: list[Rig] = []

    async def _make(n_workers: int = 1, *, slots: int = 4, fake_max: int = 4, **settings_kw):
        profiles = tmp_path / "profiles"
        kw = dict(
            redis_url="redis://unused",
            service_tokens=[SERVICE_TOKEN], run_token_secret=RUN_SECRET,
            broker_tokens=[BROKER_TOKEN],
            profiles_root=str(profiles), users_root=str(tmp_path / "users"),
            tenant_skills_root=str(tmp_path / "tenant_skills"),
            system_skills_dir=str(tmp_path / "system_skills"),
            scheduler_tick_s=0.02, worker_health_interval_s=0.2, boot_retry_s=0.2,
            hermes_reconnect_attempts=2, hermes_reconnect_backoff_s=0.05,
            hermes_start_retries=2, chat_lock_ttl_ms=3000, approval_timeout_s=5.0,
            silence_timeout_s=30.0, turn_timeout_s=60.0,
            runtime_manager_url="http://rm.fake", rm_service="kernel-harness",
            rm_secret="rm-secret-test-0001", apps_base_domain="apps.test")
        kw.update(settings_kw)
        settings = Settings(**kw)
        db = tmp_path / "vendas.db"
        make_db(db)
        hermes, served = [], []
        for _ in range(n_workers):
            fh = FakeHermes(str(profiles), max_concurrent=fake_max)
            sv = await Served(fh.app).start()
            hermes.append(fh)
            served.append(sv)
        settings.workers = [WorkerSpec(id=f"w{i + 1}", url=sv.url, slots=slots)
                            for i, sv in enumerate(served)]
        rig = Rig(tmp_path, settings, fakeredis.FakeServer(), FakeBackend(), FakeExternals(),
                  FakeEmail(), hermes, served, db_path=str(db))
        await rig.start_kernel()
        rigs.append(rig)
        return rig

    yield _make

    for rig in rigs:
        for t in rig.tasks:
            if not t.done():
                t.cancel()
        await rig.stop_kernel()
        for fh in rig.hermes:
            fh.cancel_all()
        for sv in rig.hermes_served:
            await sv.stop()


@pytest.fixture
async def rig(make_rig):
    return await make_rig(1)


@pytest.fixture
async def rig2(make_rig):
    return await make_rig(2)
