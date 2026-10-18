"""E2E da pilha completa (dev/compose.full.yml): backend REAL + Postgres + kernel-harness + Hermes real.
Cria empresa/usuarios por API master, agente por skills (rascunho -> validar -> versao -> publicar) e conversa."""
from __future__ import annotations

import json
import uuid

import httpx
import pytest

API = "http://localhost:8090/api"
MASTER = {"email": "master@example.com", "password": "admin123"}

SKILL = """---
name: saudacao
description: Como cumprimentar o cliente
---

Cumprimente de forma curta e cordial.
"""


def login(email, password):
    r = httpx.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=30)
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="module")
def world():
    m = login(**MASTER)
    key = "qa-" + uuid.uuid4().hex[:6]
    pw = "Senha-" + uuid.uuid4().hex[:8]
    r = httpx.post(f"{API}/tenants", headers=m, timeout=30, json={
        "tenant_key": key, "name": f"QA {key}", "admin_name": "Admin QA",
        "admin_email": f"admin-{key}@qa.rangeltech.net", "admin_password": pw})
    r.raise_for_status()
    tid = r.json()["id"]
    httpx.put(f"{API}/tenants/{tid}", headers=m, json={"harness_enabled": True}, timeout=30).raise_for_status()
    admin = login(f"admin-{key}@qa.rangeltech.net", pw)
    return {"master": m, "admin": admin, "tenant_id": tid, "key": key}


def test_flag_off_blocks_skills_template():
    m = login(**MASTER)
    key = "off-" + uuid.uuid4().hex[:6]
    r = httpx.post(f"{API}/tenants", headers=m, timeout=30, json={
        "tenant_key": key, "name": key, "admin_name": "A", "admin_email": f"a-{key}@qa.rangeltech.net",
        "admin_password": "Senha-12345678"})
    r.raise_for_status()
    adm = login(f"a-{key}@qa.rangeltech.net", "Senha-12345678")
    r = httpx.post(f"{API}/templates", headers=adm, json={"name": "x", "agent_kind": "skills"}, timeout=30)
    assert r.status_code == 403, r.text


def test_skills_agent_lifecycle_and_chat(world):
    h = world["admin"]
    r = httpx.post(f"{API}/templates", headers=h, timeout=30,
                   json={"name": "Atendimento", "description": "d", "agent_kind": "skills"})
    assert r.status_code in (200, 201), r.text
    tpl = r.json()["id"]
    d = httpx.get(f"{API}/templates/{tpl}/draft", headers=h, timeout=30)
    d.raise_for_status()
    files = d.json()["files"]
    assert any(f["path"] == "AGENTE.md" for f in files)
    files.append({"path": "skills/saudacao/SKILL.md", "content": SKILL})
    manifest = {"tools": ["rt_search_docs"], "environment": {"enabled": False}, "model": None, "limits": {}}
    v = httpx.post(f"{API}/templates/{tpl}/validate", headers=h, json={"files": files, "manifest": manifest}, timeout=30)
    assert v.json()["ok"] is True, v.text
    httpx.put(f"{API}/templates/{tpl}/draft", headers=h, json={"files": files, "manifest": manifest}, timeout=30).raise_for_status()
    ver = httpx.post(f"{API}/templates/{tpl}/versions", headers=h, json={"source": "draft", "notes": "v1"}, timeout=30)
    assert ver.status_code in (200, 201), ver.text
    vid = ver.json()["id"]
    httpx.post(f"{API}/templates/{tpl}/deploy", headers=h, json={"version_id": vid}, timeout=30).raise_for_status()

    # chat pela API (SSE)
    events = []
    with httpx.stream("POST", f"{API}/chat/send", headers=h, timeout=180,
                      json={"message": "ola, tudo bem?", "template_id": tpl}) as s:
        assert s.status_code == 200, s.read()
        ev = None
        for line in s.iter_lines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:") and ev:
                events.append((ev, json.loads(line[5:])))
    names = [e for e, _ in events]
    assert "chat" in names and "done" in names, names
    assert not any(e == "error" for e in names), events
    chat_id = next(d["chat_id"] for e, d in events if e == "chat")

    # mensagens persistidas
    msgs = httpx.get(f"{API}/chats/{chat_id}/messages", headers=h, timeout=30).json()
    roles = [m["role"] for m in (msgs if isinstance(msgs, list) else msgs.get("messages", []))]
    assert "user" in roles and "assistant" in roles

    # trocar de agente estruturado num chat de skills -> 409 em pt-BR
    r = httpx.get(f"{API}/chats/{chat_id}", headers=h, timeout=30)
    assert r.json().get("agent_kind") == "skills"


def test_invalid_package_is_refused(world):
    h = world["admin"]
    tpl = httpx.post(f"{API}/templates", headers=h, timeout=30,
                     json={"name": "Ruim", "agent_kind": "skills"}).json()["id"]
    files = [{"path": "skills/x/SKILL.md", "content": "sem frontmatter"}]
    v = httpx.post(f"{API}/templates/{tpl}/validate", headers=h, json={"files": files, "manifest": {"tools": []}}, timeout=30).json()
    assert v["ok"] is False
    codes = {p["code"] for p in v["problems"]}
    assert "missing_agent_md" in codes and "bad_frontmatter" in codes
    r = httpx.post(f"{API}/templates/{tpl}/versions", headers=h, json={"source": "draft"}, timeout=30)
    assert r.status_code == 422
