"""Ferramentas de ambiente, executor e agendamento (spec 04 7/9/10), offline com RM falso."""
from __future__ import annotations

import json

from tests.test_broker import last_result, tool


# ------------------------------------------------------------------ executor
async def test_executor_returns_stdout_and_registers_chart_as_artifact(rig):
    res = await last_result(rig, tool("rt_executor_run", {
        "script": "import matplotlib\nplt.savefig('/out/grafico.png')",
        "inputs": {"vendas.csv": "dia,valor\n1,9.90\n"}}))
    assert res["ok"], res
    out = res["result"]
    assert out["exit_code"] == 0 and "ok" in out["stdout"]
    art = out["artifacts"][0]
    assert art["kind"] == "image" and art["title"] == "grafico.png"
    assert rig.ext.uploads[art["artifact_id"]]  # imagem foi gravada no storage
    call = next(c for c in rig.ext.rm_calls if c["path"] == "/v1/executor/runs")
    assert call["scope"] == "*" and call["sig"] is True
    assert "vendas.csv" in call["body"]["inputs"]  # entrada em base64


async def test_executor_failure_has_hint_and_no_artifact(rig):
    rig.ext.rm_script_exit = 1
    res = await last_result(rig, tool("rt_executor_run", {"script": "raise SystemExit(1)"}))
    assert res["result"]["exit_code"] == 1 and "hint" in res["result"]
    assert res["result"]["artifacts"] == []


async def test_executor_requires_script(rig):
    res = await last_result(rig, tool("rt_executor_run", {"script": "  "}))
    assert res["error"]["code"] == "invalid_args"


# ------------------------------------------------------------------ ambiente
async def test_env_status_and_start_use_tenant_scope(rig):
    res = await last_result(rig, tool("rt_env_status", {}))
    assert res["result"]["state"] == "running"
    res = await last_result(rig, tool("rt_env_start", {}))
    assert res["ok"]
    assert all(c["scope"] == "t1" for c in rig.ext.rm_calls)


async def test_env_expose_needs_confirmation_and_denial_does_nothing(rig):
    res = await last_result(rig, tool("rt_env_expose", {"app": "metabase", "port": 3000}),
                            approve="deny")
    assert not res["ok"] and res["error"]["code"] in ("approval_denied", "approval_expired")
    assert not [c for c in rig.ext.rm_calls if c["path"].endswith("/exposures")]


async def test_env_expose_approved_publishes_and_returns_url(rig):
    res = await last_result(rig, tool("rt_env_expose", {"app": "metabase", "port": 3000}),
                            approve="approve")
    assert res["ok"], res
    assert res["result"]["url"] == "https://metabase-t1.apps.test"
    assert rig.ext.exposures["t1"] == [{"app": "metabase", "port": 3000}]
    res = await last_result(rig, tool("rt_env_status", {}))
    assert res["result"]["apps"][0]["url"] == "https://metabase-t1.apps.test"


async def test_env_expose_validates_name_and_port_before_asking(rig):
    for args in ({"app": "Meta Base", "port": 3000}, {"app": "ok", "port": 80},
                 {"app": "ok", "port": "x"}):
        res = await last_result(rig, tool("rt_env_expose", args), approve="approve")
        assert res["error"]["code"] == "invalid_args", args
    assert not rig.ext.rm_calls


async def test_env_unexpose_and_domain_flow(rig):
    await last_result(rig, tool("rt_env_expose", {"app": "site", "port": 8080}), approve="approve")
    res = await last_result(rig, tool("rt_env_domain_add", {"host": "vendas.exemplo.com.br",
                                                            "app": "site"}), approve="approve")
    dns = res["result"]["dns"]
    assert {d["type"] for d in dns} == {"TXT", "CNAME"}
    res = await last_result(rig, tool("rt_env_domain_verify", {"host": "vendas.exemplo.com.br"}))
    assert res["result"]["active"] is False
    res = await last_result(rig, tool("rt_env_unexpose", {"app": "site"}), approve="approve")
    assert res["ok"] and rig.ext.exposures["t1"] == []


async def test_env_tools_never_touch_other_tenant(rig):
    await last_result(rig, tool("rt_env_status", {}), tenant="t1")
    await last_result(rig, tool("rt_env_status", {}), tenant="t2", chat="c2")
    paths = [(c["scope"], c["path"]) for c in rig.ext.rm_calls]
    assert ("t1", "/v1/tenants/t1") in paths and ("t2", "/v1/tenants/t2") in paths
    assert not any(s == "t1" and "/t2" in p for s, p in paths)


async def test_env_tool_not_in_manifest_is_denied(rig):
    ev = await rig.send(rig.body(message=tool("rt_env_status", {}) + "[[echo]]",
                                 tools=["rt_echo"]))
    assert not rig.ext.rm_calls
    assert ev[-1].type in ("done", "error")


# --------------------------------------------------------------- agendamento
async def test_schedule_create_needs_confirmation_and_lists(rig):
    args = {"name": "Resumo diario", "cron": "0 8 * * *",
            "instruction": "Envie a cada usuario o resumo do dia por e-mail."}
    res = await last_result(rig, tool("rt_schedule_create", args), approve="deny")
    assert not res["ok"]
    assert not getattr(rig.backend, "schedules", {})
    res = await last_result(rig, tool("rt_schedule_create", args), approve="approve")
    assert res["ok"], res
    sid = res["result"]["id"]
    res = await last_result(rig, tool("rt_schedule_list", {}))
    assert [s["id"] for s in res["result"]["schedules"]] == [sid]
    res = await last_result(rig, tool("rt_schedule_pause", {"id": sid}))
    assert res["result"]["status"] == "paused"
    res = await last_result(rig, tool("rt_schedule_pause", {"id": sid, "resume": True}))
    assert res["result"]["status"] == "active"
    res = await last_result(rig, tool("rt_schedule_delete", {"id": sid}), approve="approve")
    assert res["ok"] and not rig.backend.schedules


async def test_schedule_validates_cron_and_instruction(rig):
    bad = [{"name": "x", "cron": "todo dia", "instruction": "Faça algo útil agora."},
           {"name": "x", "cron": "0 8 * * *", "instruction": "curto"},
           {"name": "", "cron": "0 8 * * *", "instruction": "Faça algo útil agora."}]
    for args in bad:
        res = await last_result(rig, tool("rt_schedule_create", args), approve="approve")
        assert res["error"]["code"] == "invalid_args", args


def test_plugin_schemas_cover_every_broker_tool():
    from broker.service import TOOLS
    from plugin.rt_broker import TOOLS as PLUGIN
    missing = set(TOOLS) - set(PLUGIN)
    assert not missing, f"sem schema no plugin: {sorted(missing)}"
    json.dumps({k: v[1] for k, v in PLUGIN.items()})
