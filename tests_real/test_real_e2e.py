"""E2E contra o Hermes REAL (dev/compose.real.yml em pe). Rodar: python -m pytest tests_real -q
Cobre: contrato basico (C), skills (S), isolamento (R), pool/fila (P). O LLM e falso e deterministico:
responde N_MSGS=<n>|USERS=...|RESULTS=<resultado das tools> e executa 'CALL:<tool>|<json>'."""
from __future__ import annotations

import concurrent.futures as cf
import json
import subprocess
import time
import uuid

import httpx
import pytest

from harness.security import sign_run_token

BASE = "http://localhost:8080"
SVC = {"Authorization": "Bearer dev-service-token"}
SECRET = "dev-run-token-secret"
AGENTE = "Voce e um agente de teste."


def skill_md(name: str, desc: str, body: str = "Instrucoes.") -> str:
    return f"---\nname: {name}\ndescription: {desc}\n---\n\n{body}\n"


def run(tenant="ta", user="u1", chat=None, message="oi", tpl="tpl", package=None, user_skills=None,
        manifest=None, timeout=120):
    rid = "r-" + uuid.uuid4().hex[:10]
    chat = chat or "c-" + uuid.uuid4().hex[:8]
    files = [{"path": "AGENTE.md", "content": AGENTE}] + (package or [])
    body = {
        "contract_version": 1, "run_id": rid, "idempotency_key": rid, "tenant_id": tenant,
        "user_id": user, "actor": {"type": "member", "name": user}, "chat_id": chat, "channel": "web",
        "template": {"id": tpl, "version_id": "v1", "kind": "skills"}, "message": message,
        "skills": {"tenant_package": {"sha256": "pkg-" + str(hash(json.dumps(files))), "files": files},
                   "user_skills": user_skills or []},
        "manifest": manifest or {"tools": ["rt_echo"]}, "credentials": {},
    }
    body["run_token"] = sign_run_token(
        {"run_id": rid, "tenant_id": tenant, "user_id": user, "exp": time.time() + 600}, SECRET)
    events = []
    with httpx.stream("POST", f"{BASE}/v1/runs", json=body, headers=SVC, timeout=timeout) as r:
        assert r.status_code == 200, r.read()
        ev = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:") and ev:
                events.append((ev, json.loads(line[5:])))
    return events


def final(events):
    for name, d in reversed(events):
        if name in ("done", "error"):
            return name, d
    return None, {}


def text_of(events):
    n, d = final(events)
    return d.get("text", "") if n == "done" else ""


def test_c1_simple_run_ends_with_done_and_usage():
    ev = run(message="ola")
    names = [e for e, _ in ev]
    assert names[-1] == "done" and "usage" in names
    assert "N_MSGS=2" in text_of(ev)


def test_c2_session_continues_in_same_chat():
    chat = "c-" + uuid.uuid4().hex[:6]
    t1 = text_of(run(chat=chat, message="meu codinome e ZORBA"))
    t2 = text_of(run(chat=chat, message="qual meu codinome?"))
    assert "N_MSGS=2" in t1 and "N_MSGS=4" in t2
    assert "ZORBA" in t2


def test_b1_tool_goes_through_broker():
    ev = run(message='x\nCALL:rt_echo|{"text":"oi-broker"}')
    assert "oi-broker" in text_of(ev)
    assert any(e == "step" and d.get("kind") == "tool" for e, d in ev)


def test_s1_company_skill_visible_to_company_only():
    marker = "emp-" + uuid.uuid4().hex[:5]
    pkg = [{"path": f"skills/{marker}/SKILL.md", "content": skill_md(marker, "skill da empresa a")}]
    a = text_of(run(tenant="ta", tpl="tplS", package=pkg, message='x\nCALL:skills_list|{}'))
    b = text_of(run(tenant="tb", tpl="tplS", message='x\nCALL:skills_list|{}'))
    assert marker in a
    assert marker not in b


def test_s2_user_skill_only_for_its_user():
    name = "u-" + uuid.uuid4().hex[:5]
    us = [{"name": name, "status": "active", "content": skill_md(name, "skill do usuario 1")}]
    httpx.put("http://localhost:8090/_test/user_skills/ta/u1", json={"skills": us}).raise_for_status()
    u1 = text_of(run(tenant="ta", user="u1", chat="s2a-"+name, user_skills=us, message='x\nCALL:skills_list|{}'))
    u2 = text_of(run(tenant="ta", user="u2", chat="s2b-"+name, message='x\nCALL:skills_list|{}'))
    assert name in u1
    assert name not in u2


def test_r1_canary_does_not_cross_tenant_or_user_or_chat():
    canary = "CANARY-" + uuid.uuid4().hex[:8]
    run(tenant="ta", user="u1", chat=("rc-"+canary), message=f"guarde {canary}")
    for t, u, c in [("tb", "u1", "rc-"+canary), ("ta", "u2", "rc-"+canary), ("ta", "u1", "rc2-"+canary)]:
        out = text_of(run(tenant=t, user=u, chat=c, message="o que voce sabe sobre CANARY?"))
        assert canary not in out, (t, u, c)
        assert "N_MSGS=2" in out, (t, u, c)


def test_p1_parallel_runs_all_complete():
    def one(i):
        return text_of(run(tenant=f"t{i % 4}", user=f"u{i % 3}", chat=f"p{i}-{uuid.uuid4().hex[:6]}", message=f"msg {i}"))
    with cf.ThreadPoolExecutor(12) as ex:
        outs = list(ex.map(one, range(24)))
    assert all("N_MSGS=2" in o for o in outs), outs


def _workers():
    r = httpx.get(f"{BASE}/internal/workers", headers=SVC, timeout=5)
    return r.json() if r.status_code == 200 else None


def test_p2_worker_killed_midrun_is_not_reported_as_done():
    chat = "kill-" + uuid.uuid4().hex[:6]
    tenant = "tk"
    # descobre o worker do tenant rodando uma run curta
    run(tenant=tenant, chat=chat, message="aquece")
    res = {}

    def slow():
        res["ev"] = run(tenant=tenant, chat=chat, message="SLEEP:25 lenta", timeout=120)
    with cf.ThreadPoolExecutor(1) as ex:
        f = ex.submit(slow)
        time.sleep(6)
        for w in ("hermes-w1", "hermes-w2"):
            subprocess.run(["docker", "kill", f"harness-real-{w}-1"], capture_output=True)
        f.result(timeout=130)
    for w in ("hermes-w1", "hermes-w2"):
        subprocess.run(["docker", "start", f"harness-real-{w}-1"], capture_output=True)
    n, d = final(res["ev"])
    assert n == "error", (n, d)  # interrompida: nunca "done"
    # volta sozinho: espera workers saudaveis e reenvia
    deadline = time.time() + 300
    last = None
    while time.time() < deadline:
        try:
            last = run(tenant=tenant, chat=chat, message="voltou?", timeout=60)
            if final(last)[0] == "done":
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(5)
    assert final(last)[0] == "done", final(last)


def test_s0_system_skills_present_in_every_profile():
    for tenant in ("ta", "tb"):
        out = text_of(run(tenant=tenant, message='x\nCALL:skills_list|{}'))
        for sk in ("pix-mercadopago", "sql-analista", "boas-praticas-de-confirmacao"):
            assert sk in out, (tenant, sk)
