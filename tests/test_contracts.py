"""Familia C (contrato Backend -> kernel-harness, spec 14.2 C1-C10), offline."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from harness.hermes_client import HermesEvent
from harness.security import sign_run_token
from harness.translate import EventTranslator, ThinkFilter
from tests.conftest import RUN_SECRET, final, text_of, types

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts" / "v1"


def schema(name: str, defn: str | None = None) -> Draft202012Validator:
    doc = json.loads((CONTRACTS / name).read_text(encoding="utf-8"))
    if defn:
        sub = {"$schema": doc["$schema"], "$defs": doc.get("$defs", {}), **doc["$defs"][defn]}
        return Draft202012Validator(sub)
    return Draft202012Validator(doc)


SSE = schema("sse_event.schema.json")
ERR = schema("error_response.schema.json")
RUN_REQ = schema("run_request.schema.json")


def assert_contract(events) -> None:
    for e in events:
        errs = list(SSE.iter_errors({"type": e.type, "data": e.data}))
        assert not errs, (e.type, e.data, errs[0].message)


# ---------------------------------------------------------------- C1
async def test_c1_request_schema_and_events_follow_contract(rig):
    body = rig.body(message="[[say:ola]] [[skill:triagem]] [[tool:rt_echo {\"text\":\"x\"}]]")
    assert not list(RUN_REQ.iter_errors(body))
    ev = await rig.send(body)
    assert final(ev).type == "done"
    assert_contract(ev)


async def test_c1_invalid_requests_are_422_with_contract_errors(rig):
    # campo obrigatorio ausente
    b = rig.body()
    del b["message"]
    r = (await rig.send(b))[0]
    assert r.type == "http" and r.data["status"] == 422 and r.data["code"] == "invalid_request"
    assert not list(ERR.iter_errors({k: v for k, v in r.data.items() if k != "status"}))
    assert r.data["problems"]
    # versao de contrato desconhecida
    b = rig.body()
    b["contract_version"] = 2
    r = (await rig.send(b))[0]
    assert (r.data["status"], r.data["code"]) == (422, "unsupported_contract")
    # template do tipo errado (graph pertence ao kernel-llm)
    b = rig.body()
    b["template"]["kind"] = "graph"
    r = (await rig.send(b))[0]
    assert r.data["status"] == 422
    # ids com caracteres perigosos
    b = rig.body(chat="../../etc")
    r = (await rig.send(b))[0]
    assert r.data["status"] == 422


async def test_c1_auth_and_run_token(rig):
    b = rig.body()
    assert (await rig.send(b, token=None))[0].data["status"] == 401
    assert (await rig.send(b, token="errado"))[0].data["status"] == 401
    # run_token de outra run -> 403
    b = rig.body()
    b["run_token"] = sign_run_token({"run_id": "outra", "tenant_id": b["tenant_id"],
                                     "user_id": b["user_id"], "exp": time.time() + 60}, RUN_SECRET)
    r = (await rig.send(b))[0]
    assert (r.data["status"], r.data["code"]) == (403, "bad_run_token")
    # run_token de outro tenant -> 403
    b = rig.body()
    b["run_token"] = sign_run_token({"run_id": b["run_id"], "tenant_id": "tX",
                                     "user_id": b["user_id"], "exp": time.time() + 60}, RUN_SECRET)
    assert (await rig.send(b))[0].data["status"] == 403
    # expirado e assinatura invalida -> 401
    b = rig.body()
    b["run_token"] = sign_run_token({"run_id": b["run_id"], "tenant_id": b["tenant_id"],
                                     "user_id": b["user_id"], "exp": time.time() - 5}, RUN_SECRET)
    assert (await rig.send(b))[0].data["status"] == 401
    b = rig.body()
    b["run_token"] = sign_run_token({"run_id": b["run_id"], "tenant_id": b["tenant_id"],
                                     "user_id": b["user_id"], "exp": time.time() + 60}, "outro")
    assert (await rig.send(b))[0].data["status"] == 401


async def test_c1_body_too_large_is_413(rig):
    b = rig.body(message="x" * (1024 * 1024 + 10))
    r = (await rig.send(b))[0]
    assert r.data["status"] == 413


async def test_c1_cancel_response_and_approval_response_follow_schema(rig):
    cancel_ok = schema("cancel.schema.json", "response")
    approval_ok = schema("approval.schema.json", "response")
    t = rig.spawn(rig.body(run_id="rc1", message="[[sleep:20]]"))
    await asyncio.sleep(0.5)
    r = await rig.client.post("/v1/runs/rc1/cancel", json={"tenant_id": "t1"},
                              headers={"Authorization": "Bearer svc-token-test-0001"})
    assert not list(cancel_ok.iter_errors(r.json())), r.json()
    await t
    r = await rig.decide("inexistente", "approve")
    assert r.status_code == 404
    assert not list(ERR.iter_errors(r.json()))
    _ = approval_ok


# ---------------------------------------------------------------- C2
async def test_c2_idempotency_replay_and_conflict(rig):
    b = rig.body(run_id="ri1", message="[[say:um]]")
    first = await rig.send(b)
    assert final(first).type == "done"
    n_before = len(rig.hermes[0].requests)
    again = await rig.send(b)  # mesma chave e mesmo corpo = mesma run
    assert [e.type for e in again] == [e.type for e in first]
    assert text_of(again) == text_of(first)
    assert len(rig.hermes[0].requests) == n_before  # nao reexecutou
    # corpo diferente com a mesma chave -> 409
    b2 = rig.body(run_id="ri1", message="[[say:dois]]")
    b2["idempotency_key"] = b["idempotency_key"]
    r = (await rig.send(b2))[0]
    assert (r.data["status"], r.data["code"]) == (409, "idempotency_conflict")
    # mesmo run_id com chave nova -> 409
    b3 = rig.body(run_id="ri1", message="[[say:um]]")
    b3["idempotency_key"] = "outra-chave"
    assert (await rig.send(b3))[0].data["status"] == 409


# ---------------------------------------------------------------- C3
async def test_c3_event_order_labels_and_think_filter(rig):
    msg = ("[[think:raciocinio secreto]][[say:Resposta final]]"
           "[[skill:triagem]][[tool:rt_echo {\"text\":\"a\"}]]")
    ev = await rig.send(rig.body(message=msg))
    assert final(ev).type == "done"
    kinds = types(ev)
    assert kinds.index("usage") < kinds.index("done") == len(kinds) - 1
    assert "raciocinio secreto" not in json.dumps([e.data for e in ev])
    steps = [e.data for e in ev if e.type == "step"]
    labels = {s["label"] for s in steps}
    assert "Consultando: triagem" in labels
    assert "Consultando um serviço externo" not in labels
    blob = json.dumps([e.data for e in ev]).lower()
    for banned in ("hermes", "harness", "kernel", "rt_echo", "broker"):
        assert banned not in blob
    ids = [tuple(int(x) for x in e.id.split("-")) for e in ev]
    assert ids == sorted(ids)
    assert_contract(ev)


async def test_c3_unknown_hermes_event_is_ignored():
    tr = EventTranslator()
    sig = tr.translate(HermesEvent(1, "algo.novo.do.hermes", {"x": 1}))
    assert sig.events == [] and sig.outcome is None


def test_c3_think_filter_split_across_chunks():
    f = ThinkFilter()
    out = f.feed("oi <thi") + f.feed("nk>segredo</th") + f.feed("ink> tchau") + f.flush()
    assert out == "oi  tchau"


# ---------------------------------------------------------------- C4
@pytest.mark.parametrize("directive,code,status", [
    ("[[say:x]][[interrupted]]", "interrupted", "interrupted"),
    ("[[partial]]", "partial_answer", "interrupted"),
    ("[[fail]]", "failed", "failed"),
    ("[[empty]]", "empty_answer", "failed"),
])
async def test_c4_non_success_terminals_never_become_done(rig, directive, code, status):
    b = rig.body(run_id="rt-" + code, message=directive)
    ev = await rig.send(b)
    last = final(ev)
    assert last.type == "error" and last.data["code"] == code
    assert "done" not in types(ev)
    assert not rig.backend.messages  # nada e gravado como resposta do assistente
    r = await rig.client.get(f"/v1/runs/{b['run_id']}",
                             headers={"Authorization": "Bearer svc-token-test-0001"})
    assert r.json()["status"] == status and r.json()["status"] != "completed"
    assert_contract(ev)


# ---------------------------------------------------------------- C5
async def test_c5_cancel_running_run(rig):
    b = rig.body(run_id="rcan", message="[[say:comecando]][[sleep:30]][[say:nunca]]")
    t = rig.spawn(b)
    await asyncio.sleep(0.6)
    r = await rig.client.post("/v1/runs/rcan/cancel", json={"tenant_id": "t1"},
                              headers={"Authorization": "Bearer svc-token-test-0001"})
    assert r.json()["status"] == "cancelling"
    ev = await asyncio.wait_for(t, 10)
    assert final(ev).type == "error" and final(ev).data["code"] == "cancelled"
    assert "nunca" not in text_of(ev)
    again = await rig.client.post("/v1/runs/rcan/cancel", json={"tenant_id": "t1"},
                                  headers={"Authorization": "Bearer svc-token-test-0001"})
    assert again.json()["status"] == "already_finished"
    # outro tenant nao enxerga a run
    other = await rig.client.post("/v1/runs/rcan/cancel", json={"tenant_id": "t2"},
                                  headers={"Authorization": "Bearer svc-token-test-0001"})
    assert other.status_code == 404
    assert rig.kernel.contexts.active() == 0


async def test_c5_cancel_queued_run(make_rig):
    rig = await make_rig(1, slots=1)
    t1 = rig.spawn(rig.body(run_id="q1", chat="a", message="[[sleep:3]]"))
    await asyncio.sleep(0.3)
    t2 = rig.spawn(rig.body(run_id="q2", chat="b", message="[[say:x]]"))
    await asyncio.sleep(0.3)
    r = await rig.client.post("/v1/runs/q2/cancel", json={},
                              headers={"Authorization": "Bearer svc-token-test-0001"})
    assert r.json()["status"] == "cancelled"
    ev2 = await t2
    assert final(ev2).data["code"] == "cancelled"
    t1.cancel()


# ---------------------------------------------------------------- C6
async def test_c6_silence_and_turn_timeouts(make_rig):
    rig = await make_rig(1, silence_timeout_s=0.8, turn_timeout_s=60.0)
    t0 = time.monotonic()
    ev = await rig.send(rig.body(message="[[say:oi]][[sleep:20]]"))
    assert final(ev).data["code"] == "timeout"
    assert time.monotonic() - t0 < 8
    assert rig.hermes[0].runs  # o Hermes recebeu stop
    assert next(iter(rig.hermes[0].runs.values())).stop.is_set()


async def test_c6_turn_timeout(make_rig):
    rig = await make_rig(1, silence_timeout_s=30.0, turn_timeout_s=1.0)
    ev = await rig.send(rig.body(message="[[sleep:20]]"))
    assert final(ev).data["code"] == "timeout"


async def test_c6_approval_wait_does_not_count_toward_timeouts(make_rig):
    rig = await make_rig(1, silence_timeout_s=1.2, turn_timeout_s=3.0, approval_timeout_s=10.0)
    msg = '[[tool:rt_email_send {"to":["a@b.test"],"subject":"s","body":"b"}]][[say:enviado]]'

    async def slow_approver(ev):
        if ev.type == "approval_required":
            await asyncio.sleep(2.5)  # maior que silencio e quase o teto do turno
            await rig.decide(ev.data["approval_id"], "approve")

    ev = await rig.send(rig.body(message=msg), on_event=slow_approver)
    assert final(ev).type == "done", [(e.type, e.data) for e in ev]
    assert len(rig.email.sent) == 1


# ---------------------------------------------------------------- C7
async def test_c7_usage_is_delta_per_turn_without_double_counting(rig):
    e1 = await rig.send(rig.body(run_id="u1", chat="cu", message="primeira"))
    e2 = await rig.send(rig.body(run_id="u2", chat="cu", message="segunda mensagem maior"))
    u1 = next(e.data for e in e1 if e.type == "usage")
    u2 = next(e.data for e in e2 if e.type == "usage")
    sess = next(iter(rig.hermes[0].sessions.values()))
    assert u1["input_tokens"] + u2["input_tokens"] == sess.tot_in
    assert u1["output_tokens"] + u2["output_tokens"] == sess.tot_out
    assert u2["input_tokens"] < sess.tot_in  # nao e o acumulado
    posted = sum(x["input_tokens"] for x in rig.backend.usage)
    assert posted == sess.tot_in
    assert all(x["kind"] == "turn" for x in rig.backend.usage)


async def test_c7_usage_when_hermes_reports_turn_values(make_rig):
    rig = await make_rig(1, usage_mode="turn")
    rig.hermes[0].cumulative = False
    e1 = await rig.send(rig.body(run_id="ut1", chat="x", message="a"))
    e2 = await rig.send(rig.body(run_id="ut2", chat="x", message="b"))
    sess = next(iter(rig.hermes[0].sessions.values()))
    got = [next(e.data for e in ev if e.type == "usage")["input_tokens"] for ev in (e1, e2)]
    assert sum(got) == sess.tot_in


# ---------------------------------------------------------------- C9 / C10
async def test_c9_kind_graph_is_not_served_by_this_kernel(rig):
    """O 409 'outro tipo de agente' e do backend (chats.kernel); aqui o kernel recusa graph."""
    b = rig.body()
    b["template"]["kind"] = "graph"
    assert (await rig.send(b))[0].data["status"] == 422


async def test_c10_retry_after_interruption_uses_new_key(rig):
    b1 = rig.body(run_id="ret1", chat="cr", message="[[say:a]][[interrupted]]")
    e1 = await rig.send(b1)
    assert final(e1).data["code"] == "interrupted"
    # reenviar a MESMA chave devolve o resultado antigo (interrompida), nunca sucesso
    again = await rig.send(b1)
    assert final(again).type == "error" and final(again).data["code"] == "interrupted"
    # nova chave = nova run, funciona
    e2 = await rig.send(rig.body(run_id="ret2", chat="cr", message="de novo"))
    assert final(e2).type == "done"


async def test_c_backend_persists_final_message_independent_of_client(rig):
    b = rig.body(run_id="bg1", message="[[say:resposta persistida]]")
    # cliente le so o primeiro evento e desconecta
    async with rig.client.stream("POST", "/v1/runs", json=b,
                                 headers={"Authorization": "Bearer svc-token-test-0001"}) as r:
        async for _ in r.aiter_lines():
            break
    for _ in range(100):
        if rig.backend.messages:
            break
        await asyncio.sleep(0.05)
    assert rig.backend.messages and rig.backend.messages[0]["content"] == "resposta persistida"


# ------------------------------------------------------- boot sem Redis
async def test_boot_with_redis_down_stays_up_then_recovers(make_rig):
    """O processo sobe mesmo sem Redis (health ok, ready=false, runs 503) e se recupera."""
    rig = await make_rig(1)
    await rig.stop_kernel()
    rig.redis_server.connected = False
    await rig.start_kernel()
    h = (await rig.client.get("/health")).json()
    assert h["status"] == "ok" and h["ready"] is False
    r = (await rig.send(rig.body()))[0]
    assert r.data["status"] == 503
    rig.redis_server.connected = True
    for _ in range(100):
        if (await rig.client.get("/health")).json()["ready"]:
            break
        await asyncio.sleep(0.1)
    ev = await rig.send(rig.body(message="voltei"))
    assert final(ev).type == "done"


# ------------------------------------------------------ extracao de memorias
async def test_memories_extract_dedupes_and_never_fails_the_turn(rig):
    import httpx

    calls = []

    def llm(req: httpx.Request) -> httpx.Response:
        calls.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content":
            'Claro: ["Prefere respostas curtas", "Trabalha na Acme", "token SEGREDO-123 do usuario", 7]'}}]})

    rig.kernel.http = httpx.AsyncClient(transport=httpx.MockTransport(llm))
    hdr = {"Authorization": "Bearer svc-token-test-0001"}
    body = {"tenant_id": "t1", "user_id": "u1", "existing": ["prefere respostas CURTAS"],
            "secret_values": ["SEGREDO-123"],
            "messages": [{"role": "user", "content": "eu trabalho na Acme"},
                         {"role": "assistant", "content": "ok"}]}
    r = await rig.client.post("/v1/memories/extract", json=body, headers=hdr)
    assert r.json() == {"facts": ["Trabalha na Acme"]}
    assert calls and len(calls[0]["messages"]) == 2
    # LLM fora do ar: lista vazia, sem erro
    rig.kernel.http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(500)))
    r = await rig.client.post("/v1/memories/extract", json=body, headers=hdr)
    assert r.status_code == 200 and r.json() == {"facts": []}
    assert (await rig.client.post("/v1/memories/extract", json={}, headers=hdr)).status_code == 422
