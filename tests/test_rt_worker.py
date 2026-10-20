"""Front do worker efemero (rt_worker.front) com spawner falso: contrato, slots, limpeza, idempotencia."""
from __future__ import annotations

import asyncio
import json
import signal

import aiohttp
import pytest
from aiohttp import web

from rt_worker.front import Config, Front


class FakeSpawner:
    """Cada `spawn` sobe um servidor aiohttp na porta pedida que imita o API server de UMA run."""

    def __init__(self, run_delay: float = 0.0, fail: bool = False):
        self.alive_pids: set[int] = set()
        self.servers: dict[int, web.AppRunner] = {}
        self.killed: list[tuple[int, int]] = []
        self.spawned: list[tuple[str, int]] = []
        self.run_delay = run_delay
        self.fail = fail
        self._pid = 1000
        self.is_ready = True

    async def spawn(self, profile: str, port: int, deadline_s: float) -> int:
        if self.fail:
            raise RuntimeError("zigoto fora")
        self._pid += 1
        pid = self._pid
        self.spawned.append((profile, port))
        app = web.Application()
        n = {"i": 0}

        async def start(request: web.Request) -> web.Response:
            n["i"] += 1
            return web.json_response({"run_id": f"run_{pid}", "status": "started"}, status=202)

        async def events(request: web.Request) -> web.StreamResponse:
            resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            await resp.write(b': open\n\n')
            await asyncio.sleep(self.run_delay)
            ev = {"event": "run.completed", "output": "ok", "seq": 0}
            await resp.write(b"id: 0\ndata: " + json.dumps(ev).encode() + b"\n\n")
            return resp

        async def other(request: web.Request) -> web.Response:
            return web.json_response({"echo": request.match_info["tail"]})

        app.router.add_get("/health", lambda r: web.json_response({"status": "ok"}))
        app.router.add_post(f"/p/{profile}/v1/runs", start)
        app.router.add_get(f"/p/{profile}/v1/runs/run_{pid}/events", events)
        app.router.add_post(f"/p/{profile}/v1/runs/run_{pid}/{{tail}}", other)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        self.servers[pid] = runner
        self.alive_pids.add(pid)
        return pid

    def ready(self) -> bool:
        return self.is_ready

    def alive(self, pid: int) -> bool:
        return pid in self.alive_pids

    def kill(self, pid: int, sig: int) -> None:
        self.killed.append((pid, sig))
        self.alive_pids.discard(pid)
        runner = self.servers.pop(pid, None)
        if runner is not None:
            asyncio.get_running_loop().create_task(runner.cleanup())


@pytest.fixture
def profiles(tmp_path):
    for p in ("u-aaa", "u-bbb"):
        (tmp_path / p).mkdir()
        (tmp_path / p / "config.yaml").write_text("x: 1")
    return tmp_path


async def make(profiles, sp, **kw):
    cfg = Config(profiles_root=str(profiles), port_lo=23100, port_hi=23140, grace_s=0.2,
                 kill_after_s=0.2, sweep_s=3600, **kw)
    front = Front(cfg, sp)
    await front.start()
    client = aiohttp.ClientSession()
    runner = web.AppRunner(front.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 23099).start()
    return front, client, runner


H = {"Authorization": "Bearer k"}
BASE = "http://127.0.0.1:23099"


async def test_fluxo_completo_e_filho_morre_apos_terminal(profiles):
    sp = FakeSpawner()
    front, client, runner = await make(profiles, sp, slots=2)
    try:
        r = await client.post(f"{BASE}/p/u-aaa/v1/runs", json={"input": "oi"}, headers=H)
        assert r.status == 202
        rid = (await r.json())["run_id"]
        assert front.active == 1
        r = await client.get(f"{BASE}/p/u-aaa/v1/runs/{rid}/events", headers=H)
        body = await r.read()
        assert b"run.completed" in body
        await asyncio.sleep(0.3)  # passa a graca
        front.sweep()
        assert (1001, signal.SIGTERM) in sp.killed
        await asyncio.sleep(0.3)
        front.sweep()  # morreu: libera o slot e a porta
        assert front.active == 0 and not front.runs
        assert len(front.ports_free) == 41
    finally:
        await client.close()
        await runner.cleanup()
        await front.stop()


async def test_slots_cheios_devolvem_429_com_retry_after(profiles):
    sp = FakeSpawner(run_delay=5)
    front, client, runner = await make(profiles, sp, slots=1)
    try:
        r1 = await client.post(f"{BASE}/p/u-aaa/v1/runs", json={"input": "a"}, headers=H)
        assert r1.status == 202
        r2 = await client.post(f"{BASE}/p/u-bbb/v1/runs", json={"input": "b"}, headers=H)
        assert r2.status == 429
        assert r2.headers["Retry-After"] == "1"
        assert front.stats["rejected"] == 1
        assert len(sp.spawned) == 1  # a rejeitada nao gastou filho
    finally:
        await client.close()
        await runner.cleanup()
        await front.stop()


async def test_perfil_desconhecido_e_nome_malicioso(profiles):
    sp = FakeSpawner()
    front, client, runner = await make(profiles, sp, slots=2)
    try:
        for name in ("u-zzz", "..%2Fetc", "U-AAA", "a" * 80):
            r = await client.post(f"{BASE}/p/{name}/v1/runs", json={"input": "a"}, headers=H)
            assert r.status == 404, name
        r = await client.post(f"{BASE}/p/u-aaa/v1/runs", json={"input": "a"})
        assert r.status == 401
        assert sp.spawned == []
    finally:
        await client.close()
        await runner.cleanup()
        await front.stop()


async def test_idempotencia_repete_run_sem_novo_filho(profiles):
    sp = FakeSpawner(run_delay=5)
    front, client, runner = await make(profiles, sp, slots=4)
    try:
        h = {**H, "Idempotency-Key": "abc"}
        a = await (await client.post(f"{BASE}/p/u-aaa/v1/runs", json={"input": "x"}, headers=h)).json()
        b = await (await client.post(f"{BASE}/p/u-aaa/v1/runs", json={"input": "x"}, headers=h)).json()
        assert a["run_id"] == b["run_id"] and b.get("replayed") is True
        assert len(sp.spawned) == 1
    finally:
        await client.close()
        await runner.cleanup()
        await front.stop()


async def test_falha_do_zigoto_vira_503_e_devolve_porta(profiles):
    sp = FakeSpawner(fail=True)
    front, client, runner = await make(profiles, sp, slots=2)
    try:
        r = await client.post(f"{BASE}/p/u-aaa/v1/runs", json={"input": "a"}, headers=H)
        assert r.status == 503 and r.headers["Retry-After"] == "1"
        assert front.active == 0 and len(front.ports_free) == 41
    finally:
        await client.close()
        await runner.cleanup()
        await front.stop()


async def test_roteia_stop_para_o_filho_certo_e_404_para_run_alheia(profiles):
    sp = FakeSpawner(run_delay=5)
    front, client, runner = await make(profiles, sp, slots=4)
    try:
        a = await (await client.post(f"{BASE}/p/u-aaa/v1/runs", json={"input": "x"}, headers=H)).json()
        r = await client.post(f"{BASE}/p/u-aaa/v1/runs/{a['run_id']}/stop", headers=H)
        assert r.status == 200 and (await r.json()) == {"echo": "stop"}
        # outro perfil nao enxerga a run
        r = await client.post(f"{BASE}/p/u-bbb/v1/runs/{a['run_id']}/stop", headers=H)
        assert r.status == 404
        r = await client.post(f"{BASE}/p/u-aaa/v1/runs/run_nope/stop", headers=H)
        assert r.status == 404
    finally:
        await client.close()
        await runner.cleanup()
        await front.stop()


async def test_teto_da_run_mata_filho_sem_evento_terminal(profiles):
    sp = FakeSpawner(run_delay=30)
    front, client, runner = await make(profiles, sp, slots=2, run_max_s=0.1)
    try:
        await client.post(f"{BASE}/p/u-aaa/v1/runs", json={"input": "x"}, headers=H)
        await asyncio.sleep(0.5)
        front.sweep()
        assert any(s == signal.SIGTERM for _, s in sp.killed)
    finally:
        await client.close()
        await runner.cleanup()
        await front.stop()


def test_health_e_lista_de_modulos_quentes():
    from rt_worker.zygote import warm_module_names
    names = warm_module_names()
    assert len(names) > 100 and all(" " not in n for n in names)


async def test_health_do_worker_so_cai_apos_falhas_seguidas(make_rig):
    rig = await make_rig(1, worker_probe_failures=3)
    reg = rig.kernel.workers
    w = next(iter(reg.workers.values()))

    class Boom:
        async def get(self, *a, **k):
            import httpx
            raise httpx.ConnectTimeout("lento")

    good = reg._http
    reg._http = Boom()
    assert await reg.probe(w) is True and w.healthy  # 1a falha: segue saudavel
    assert await reg.probe(w) is True and w.healthy
    assert await reg.probe(w) is False and not w.healthy  # 3a: fora
    reg._http = good
    assert await reg.probe(w) is True and w.healthy and w.probe_failures == 0


async def test_eta_da_fila_cresce_com_a_posicao(make_rig):
    rig = await make_rig(1, slots=4, run_est_s=20.0)
    s = rig.kernel.scheduler
    assert s.eta_s(1) < s.eta_s(9) <= 20 * 9 / 4 + 2


async def test_health_503_enquanto_o_zigoto_aquece(profiles):
    sp = FakeSpawner()
    sp.is_ready = False
    front, client, runner = await make(profiles, sp, slots=2)
    try:
        r = await client.get(f"{BASE}/health")
        assert r.status == 503 and (await r.json())["status"] == "warming"
        sp.is_ready = True
        assert (await client.get(f"{BASE}/health")).status == 200
    finally:
        await client.close()
        await runner.cleanup()
        await front.stop()
