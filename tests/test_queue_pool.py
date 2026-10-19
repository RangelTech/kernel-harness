"""Familia P (fila Redis, dispatchers, pool, justica, recuperacao; spec 14.2 P1-P10), offline."""
from __future__ import annotations

import asyncio
import statistics
import time
from collections import defaultdict

from harness.profiles import profile_name
from tests.conftest import Ev, final, text_of, types

AUTH = {"Authorization": "Bearer svc-token-test-0001"}


def hermes_runs(rig):
    """(worker_idx, FakeRun) de todas as runs recebidas pelos Hermes falsos."""
    out = []
    for i, h in enumerate(rig.hermes):
        out += [(i, r) for r in h.runs.values()]
    return out


# ---------------------------------------------------------------------- P1
async def test_p1_order_per_chat_three_quick_messages(rig):
    msgs = ["[[say:A]][[sleep:0.3]]", "[[say:B]][[sleep:0.1]]", "[[say:C]]"]
    tasks = []
    for i, m in enumerate(msgs):
        tasks.append(rig.spawn(rig.body(run_id=f"o{i}", chat="mesmo", message=m)))
        await asyncio.sleep(0.03)  # chegada rapida, em ordem
    results = await asyncio.gather(*tasks)
    assert [text_of(r) for r in results] == ["A", "B", "C"]
    runs = sorted((r for _, r in hermes_runs(rig)), key=lambda r: r.t0)
    inputs = [r.text for r in runs]
    assert inputs == msgs  # executou na ordem de chegada
    for a, b in zip(runs, runs[1:], strict=False):
        assert b.t0 >= a.t1  # nunca em paralelo no mesmo chat
    assert not [k async for k in rig.kernel.redis.scan_iter(match="lock:chat:*")]


async def test_p1_different_chats_run_in_parallel(rig):
    t0 = time.monotonic()
    rs = await asyncio.gather(*(rig.send(rig.body(chat=f"c{i}", message="[[sleep:0.6]][[say:x]]"))
                                for i in range(4)))
    assert all(final(r).type == "done" for r in rs)
    assert time.monotonic() - t0 < 2.4  # 4 x 0.6s em paralelo, nao em serie


# ---------------------------------------------------------------------- P2
async def test_p2_fairness_noisy_tenant_does_not_starve_another(make_rig):
    rig = await make_rig(1, slots=2, tenant_concurrency=2)
    noisy = [rig.spawn(rig.body(run_id=f"a{i}", tenant="A", user=f"ua{i}", chat=f"ca{i}",
                                message="[[sleep:0.5]][[say:ruidosa]]")) for i in range(8)]
    await asyncio.sleep(0.15)
    quiet = rig.spawn(rig.body(run_id="b0", tenant="B", user="ub", chat="cb",
                               message="[[sleep:0.1]][[say:quieta]]"))
    await asyncio.gather(quiet, *noisy)
    order = sorted((r.t0, r.text) for _, r in hermes_runs(rig))
    pos_b = next(i for i, (_, t) in enumerate(order) if "quieta" in t)
    assert pos_b <= 4, order  # nao esperou as 8 do tenant barulhento
    assert final(await quiet).type == "done"


async def test_p2_tenant_concurrency_cap(make_rig):
    rig = await make_rig(1, slots=8, tenant_concurrency=2)
    peak = defaultdict(int)
    stop = asyncio.Event()

    async def sampler():
        while not stop.is_set():
            for t, n in rig.kernel.scheduler.running.items():
                peak[t] = max(peak[t], n)
            await asyncio.sleep(0.01)

    s = asyncio.create_task(sampler())
    rs = await asyncio.gather(*(rig.send(rig.body(tenant="A", user=f"u{i}", chat=f"c{i}",
                                                  message="[[sleep:0.3]][[say:x]]")) for i in range(6)))
    stop.set()
    await s
    assert all(final(r).type == "done" for r in rs)
    assert peak["A"] == 2


async def test_p2_channel_priority_external_before_internal(make_rig):
    rig = await make_rig(1, slots=1, tenant_concurrency=5)
    block = rig.spawn(rig.body(run_id="blk", tenant="X", chat="b", message="[[sleep:0.8]]"))
    await asyncio.sleep(0.3)
    web = rig.spawn(rig.body(run_id="w1", tenant="Y", user="u", chat="w", message="web"))
    await asyncio.sleep(0.05)
    ext = rig.spawn(rig.body(run_id="e1", tenant="Z", user=None, chat="e", channel="chatwoot",
                             actor_type="external", message="externo"))
    await asyncio.gather(block, web, ext)
    order = [r.text for r in sorted((r for _, r in hermes_runs(rig)), key=lambda r: r.t0)]
    assert order.index("externo") < order.index("web")


# ---------------------------------------------------------------------- P3
async def test_p3_queue_position_and_expiry(make_rig):
    rig = await make_rig(1, slots=1, queue_expiry_s=0.8)
    hold = rig.spawn(rig.body(run_id="h1", chat="h", message="[[sleep:3]]"))
    await asyncio.sleep(0.3)
    t0 = time.monotonic()
    ev = await rig.send(rig.body(run_id="w1", chat="w", message="espera"))
    assert time.monotonic() - t0 < 4
    queue_steps = [e.data for e in ev if e.type == "step" and e.data["kind"] == "queue"]
    assert queue_steps and queue_steps[0]["label"] == "Na fila (posição 1)"
    assert queue_steps[0]["position"] == 1
    assert final(ev).data["code"] == "queue_expired"
    r = await rig.client.get("/v1/runs/w1", headers=AUTH)
    assert r.json()["status"] == "expired"
    hold.cancel()


async def test_p3_position_advances_and_announces_start(make_rig):
    rig = await make_rig(1, slots=1)
    a = rig.spawn(rig.body(run_id="p1", chat="1", message="[[sleep:0.5]][[say:a]]"))
    await asyncio.sleep(0.2)
    b = rig.spawn(rig.body(run_id="p2", chat="2", message="b"))
    evb = await b
    await a
    steps = [(e.data["label"], e.data["status"]) for e in evb
             if e.type == "step" and e.data["kind"] == "queue"]
    assert ("Na fila (posição 1)", "start") in steps and ("Iniciando", "done") in steps
    assert final(evb).type == "done"


async def test_p3_backpressure_returns_429_with_retry_after(make_rig):
    rig = await make_rig(1, slots=1, queue_max_per_tenant=2)
    hold = rig.spawn(rig.body(run_id="bp0", chat="0", message="[[sleep:2]]"))
    await asyncio.sleep(0.3)
    waiting = [rig.spawn(rig.body(run_id=f"bp{i}", chat=str(i), message="x")) for i in (1, 2)]
    await asyncio.sleep(0.3)
    r = (await rig.send(rig.body(run_id="bp9", chat="9", message="x")))[0]
    assert (r.data["status"], r.data["code"]) == (429, "backpressure")
    for t in (hold, *waiting):
        t.cancel()


# ---------------------------------------------------------------------- 429
async def test_worker_429_goes_back_to_queue_and_everything_finishes(make_rig):
    rig = await make_rig(1, slots=4, fake_max=1, hermes_start_retries=1)
    rs = await asyncio.gather(*(rig.send(rig.body(chat=f"c{i}", user=f"u{i}",
                                                  message=f"[[sleep:0.3]][[say:r{i}]]"))
                                for i in range(4)))
    assert all(final(r).type == "done" for r in rs), [(final(r).type, final(r).data) for r in rs]
    assert not any(final(r).data.get("code") == "busy" for r in rs)
    assert rig.hermes[0].active == 0


# ---------------------------------------------------------------------- P4
async def test_p4_thirty_simultaneous_runs_all_finish_within_p95_budget(make_rig):
    rig = await make_rig(2, slots=8, tenant_concurrency=4)
    t0 = time.monotonic()

    async def one(i):
        s = time.monotonic()
        ev = await rig.send(rig.body(tenant=f"t{i % 6}", user=f"u{i}", chat=f"c{i}",
                                     message=f"[[sleep:0.2]][[say:run{i}]]"))
        return final(ev).type, time.monotonic() - s

    res = await asyncio.gather(*(one(i) for i in range(30)))
    assert [r[0] for r in res] == ["done"] * 30
    lat = sorted(r[1] for r in res)
    assert lat[int(0.95 * len(lat)) - 1] < 20  # p95 dentro da meta (fake, sem LLM)
    assert time.monotonic() - t0 < 40
    assert statistics.median(lat) < 10
    # nada vazou
    await asyncio.sleep(0.2)
    assert rig.kernel.contexts.active() == 0 and len(rig.kernel.pending) == 0
    assert not rig.kernel.scheduler.queues and not rig.kernel.scheduler.running
    assert not [k async for k in rig.kernel.redis.scan_iter(match="lock:chat:*")]
    assert all(h.active == 0 for h in rig.hermes)


# ---------------------------------------------------------------------- P5
async def test_p5_worker_killed_midrun_is_interrupted_then_reassigned_with_fencing(rig2):
    rig = rig2
    tenant = "tk"
    t = rig.spawn(rig.body(run_id="k1", tenant=tenant, chat="c1", message="[[say:antes]][[sleep:30]]"))
    await asyncio.sleep(0.8)
    ctx = rig.kernel.contexts.by_run("k1")
    victim = ctx.worker_id
    idx = 0 if victim == "w1" else 1
    rig.hermes[idx].cancel_all()
    await rig.hermes_served[idx].kill()
    ev = await asyncio.wait_for(t, 20)
    assert final(ev).type == "error" and final(ev).data["code"] == "interrupted"
    assert "done" not in types(ev)  # nunca sucesso
    r = await rig.client.get("/v1/runs/k1", headers=AUTH)
    assert r.json()["status"] == "interrupted"
    assert rig.kernel.workers.workers[victim].healthy is False
    # proxima run do mesmo tenant vai para o outro worker e o fencing e pedido antes
    ev2 = await rig.send(rig.body(run_id="k2", tenant=tenant, chat="c1", message="depois"))
    assert final(ev2).type == "done"
    survivor = "w2" if victim == "w1" else "w1"
    assert rig.kernel.workers.affinity_of(tenant) == survivor
    assert (victim, tenant) in rig.kernel.workers.fencer.calls
    assert rig.kernel.contexts.active() == 0


async def test_p5_worker_dead_before_start_run_is_reassigned_not_failed(rig2):
    rig = rig2
    tenant = "tz"
    first = await rig.send(rig.body(run_id="z0", tenant=tenant, chat="c", message="aquece"))
    assert final(first).type == "done"
    victim = rig.kernel.workers.affinity_of(tenant)
    idx = 0 if victim == "w1" else 1
    rig.kernel.workers.workers[victim].spec.slots = 4
    await rig.hermes_served[idx].kill()  # cai ANTES de a saude perceber
    rig.kernel.workers.workers[victim].healthy = True
    ev = await rig.send(rig.body(run_id="z1", tenant=tenant, chat="c", message="segue"))
    assert final(ev).type == "done", [(e.type, e.data) for e in ev]
    assert rig.kernel.workers.affinity_of(tenant) != victim


async def test_p5_no_healthy_worker_gives_friendly_expiry(make_rig):
    rig = await make_rig(1, queue_expiry_s=0.6)
    await rig.hermes_served[0].kill()
    rig.kernel.workers.workers["w1"].healthy = False
    ev = await rig.send(rig.body(message="oi"))
    assert final(ev).data["code"] == "queue_expired"


# ---------------------------------------------------------------------- P6
async def test_p6_redis_loses_state_queue_recovers_from_memory(make_rig):
    rig = await make_rig(1, slots=1)
    hold = rig.spawn(rig.body(run_id="rd0", chat="0", message="[[say:um]][[sleep:1.2]]"))
    await asyncio.sleep(0.4)
    waiting = rig.spawn(rig.body(run_id="rd1", chat="1", message="[[say:dois]]"))
    await asyncio.sleep(0.3)
    await rig.kernel.redis.flushall()  # Redis voltou vazio (sem AOF)
    ev0, ev1 = await asyncio.wait_for(asyncio.gather(hold, waiting), 20)
    assert final(ev0).type == "done" and final(ev1).type == "done", (ev0[-1], ev1[-1])
    assert text_of(ev1) == "dois"


# ---------------------------------------------------------------------- P7
async def test_p7_kernel_restart_graceful_interrupts_inflight_and_queued(make_rig):
    rig = await make_rig(1, slots=1)
    running = rig.spawn(rig.body(run_id="kr1", chat="1", message="[[say:a]][[sleep:30]]"))
    await asyncio.sleep(0.5)
    queued = rig.spawn(rig.body(run_id="kr2", chat="2", message="fila"))
    await asyncio.sleep(0.3)
    await rig.restart_kernel()
    await asyncio.gather(running, queued, return_exceptions=True)
    for rid in ("kr1", "kr2"):
        ev = await rig.send(rig.body(), path=f"/v1/runs/{rid}/events", method="GET")
        assert final(ev).type == "error", rid
        assert final(ev).data["code"] in ("interrupted", "kernel_restarted")
        assert "done" not in types(ev)
        st = await rig.client.get(f"/v1/runs/{rid}", headers=AUTH)
        assert st.json()["status"] == "interrupted"
    # volta a servir normalmente
    ev = await rig.send(rig.body(run_id="kr3", chat="1", message="de novo"))
    assert final(ev).type == "done"
    assert rig.hermes[0].active in (0, 1)  # a run antiga recebeu stop; nada novo travado


async def test_p7_kernel_hard_crash_recovery_marks_orphans_interrupted(make_rig):
    rig = await make_rig(1, slots=1)
    # simula crash: nenhum handler de encerramento consegue publicar nada
    rig.kernel.dispatcher._terminal_error = _noop
    running = rig.spawn(rig.body(run_id="hc1", chat="1", message="[[sleep:30]]"))
    await asyncio.sleep(0.5)
    queued = rig.spawn(rig.body(run_id="hc2", chat="2", message="x"))
    await asyncio.sleep(0.3)
    await rig.restart_kernel()
    await asyncio.gather(running, queued, return_exceptions=True)
    for rid in ("hc1", "hc2"):
        ev = await rig.send(rig.body(), path=f"/v1/runs/{rid}/events", method="GET")
        assert final(ev).type == "error" and final(ev).data["code"] == "interrupted", rid
        st = await rig.client.get(f"/v1/runs/{rid}", headers=AUTH)
        assert st.json()["status"] == "interrupted"
        assert st.json()["error_code"] == "kernel_restarted"


async def _noop(*_a, **_k):
    return None


# ---------------------------------------------------------------------- P8
async def test_p8_sse_reconnect_with_last_event_id_has_no_gap_or_duplicate(rig):
    b = rig.body(run_id="sse1", message="[[say:um dois tres quatro cinco seis sete oito]]")
    full = await rig.send(b)
    assert final(full).type == "done" and len(full) > 5
    cut = full[3]  # reconecta depois do 4o evento
    tail = await rig.send({}, path="/v1/runs/sse1/events", method="GET",
                          headers={"Last-Event-ID": cut.id})
    assert [e.id for e in tail] == [e.id for e in full[4:]]
    assert text_of(full[:4]) + text_of(tail) == text_of(full)


async def test_p8_reconnect_while_running_streams_the_rest(rig):
    b = rig.body(run_id="sse2", message="[[say:antes]][[sleep:1.0]][[say:depois]]")
    first: list[Ev] = []

    async def grab(ev):
        first.append(ev)

    t = rig.spawn(b, on_event=grab)
    await asyncio.sleep(0.5)
    assert first  # ja recebeu eventos
    last = first[-1].id
    rest = await rig.send({}, path="/v1/runs/sse2/events", method="GET",
                          headers={"Last-Event-ID": last})
    await t
    assert final(rest).type == "done" and text_of(rest).endswith("depois")


async def test_p8_events_of_other_tenant_are_hidden(rig):
    await rig.send(rig.body(run_id="priv1", tenant="tA", message="segredo"))
    r = await rig.client.get("/v1/runs/priv1/events", params={"tenant_id": "tB"}, headers=AUTH)
    assert r.status_code == 404


# ---------------------------------------------------------------------- P9
async def test_p9_single_writer_per_tenant_profiles(rig2):
    rig = rig2
    jobs = []
    for tenant in ("ta", "tb", "tc", "td", "te", "tf", "tg", "th"):
        for u in range(3):
            for tpl in ("tpl1", "tpl2"):
                jobs.append(rig.send(rig.body(tenant=tenant, user=f"u{u}", template=tpl,
                                              chat=f"c{u}{tpl}", message="oi")))
    res = await asyncio.gather(*jobs)
    assert all(final(r).type == "done" for r in res)
    writers = defaultdict(set)
    for i, h in enumerate(rig.hermes):
        for req in h.requests:
            tenant = req["profile"].split("__")[0]
            writers[tenant].add(i)
    assert writers and all(len(w) == 1 for w in writers.values()), dict(writers)
    # e os 4 tenants se espalharam (afinidade por hash, nao tudo no mesmo worker)
    assert {next(iter(w)) for w in writers.values()} == {0, 1}


async def test_p9_profile_name_is_per_company_template_and_user(rig):
    assert profile_name("t1", "tpl", "u1") != profile_name("t1", "tpl", "u2")
    assert profile_name("t1", "tpl", "u1") != profile_name("t1", "tpl2", "u1")
    assert profile_name("t1", "tpl", "u1") != profile_name("t2", "tpl", "u1")
    assert profile_name("t1", "tpl", None).endswith("__shared")
    await rig.send(rig.body(tenant="t1", user="u1", message="a"))
    await rig.send(rig.body(tenant="t1", user=None, actor_type="external", channel="api",
                            message="b"))
    profiles = {r["profile"] for r in rig.hermes[0].requests}
    assert profiles == {"t1__tpl1__u1", "t1__tpl1__shared"}


# --------------------------------------------------------------------- P10
async def test_p10_burst_of_100_runs_leaves_no_leak(make_rig):
    rig = await make_rig(3, slots=6, tenant_concurrency=6)
    jobs = [rig.send(rig.body(tenant=f"t{i % 10}", user=f"u{i % 20}", chat=f"c{i}",
                              message="[[sleep:0.05]][[say:rajada]]")) for i in range(100)]
    res = await asyncio.gather(*jobs)
    done = sum(1 for r in res if final(r).type == "done")
    assert done == 100, [final(r).data for r in res if final(r).type != "done"][:3]
    await asyncio.sleep(0.3)
    assert rig.kernel.contexts.active() == 0
    assert len(rig.kernel.pending) == 0
    assert all(w.in_use == 0 for w in rig.kernel.workers.workers.values())
    assert not rig.kernel.scheduler.queues
    assert sum(1 for h in rig.hermes for _ in h.runs) == 100


async def test_h1_model_429_before_any_action_is_retried_then_completes(make_rig):
    rig = await make_rig(1, model_retry_delay_s=0.05)
    ev = await rig.send(rig.body(run_id="rl0", message="[[fail429:2]][[say:pronto]]"))
    assert final(ev).type == "done", [(e.type, e.data) for e in ev]
    assert text_of(ev) == "pronto"
    assert any(e.type == "step" and e.data.get("label") == "Aguardando o modelo liberar" for e in ev)


async def test_h1_persistent_429_ends_with_friendly_message_never_success(make_rig):
    rig = await make_rig(1, model_retry_delay_s=0.05)
    ev = await rig.send(rig.body(run_id="rl1", message="[[fail429:99]][[say:nunca]]"))
    last = final(ev)
    assert last.type == "error" and last.data["code"] == "rate_limited"
    assert "muitas solicitações" in last.data["detail"]
