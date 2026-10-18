"""Ferramentas de ambiente, executor e agendamento (spec 04 7/9/10), offline com RM falso."""
from __future__ import annotations

import json

from tests.conftest import S_MP
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
    assert rig.backend.files[art["artifact_id"]]  # imagem foi entregue ao backend
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


async def test_capability_group_in_manifest_allows_its_tools(rig):
    res = await last_result(rig, tool("rt_env_expose", {"app": "grp", "port": 4000}),
                            approve="approve", tools=["rt_env_manage"])
    assert res["ok"], res
    res = await last_result(rig, tool("rt_schedule_list", {}), tools=["rt_job_schedule"])
    assert res["ok"], res


def test_expand_tools_dedups_and_keeps_plain_names():
    from broker.catalog import expand_tools
    out = expand_tools(["rt_env_manage", "rt_env_expose", "rt_echo"])
    assert out.count("rt_env_expose") == 1 and "rt_echo" in out and "rt_env_domain_add" in out


async def test_scheduled_run_preapproves_email_but_denies_money_and_publish(rig):
    ev = await rig.send(rig.body(message=tool("rt_email_send", {
        "account": "Financeiro", "to": ["a@b.test"], "subject": "Resumo", "body": "oi"}) + "[[echo]]",
        channel="schedule"))
    assert ev[-1].type == "done", [(e.type, e.data) for e in ev]
    res = json.loads(ev[-1].data["text"])
    assert res["ok"], res
    res = await last_result(rig, tool("rt_env_expose", {"app": "x1", "port": 4000}),
                            channel="schedule")
    assert not res["ok"] and not [c for c in rig.ext.rm_calls if c["path"].endswith("/exposures")]


async def test_env_first_use_creates_environment_then_exec_runs(rig):
    rig.ext.rm_autocreate = False
    res = await last_result(rig, tool("rt_env_status", {}))
    assert res["result"]["state"] == "not_created"
    res = await last_result(rig, tool("rt_env_start", {}))
    assert res["result"]["state"] == "running", res
    created = [c for c in rig.ext.rm_calls if c["method"] == "POST" and c["path"] == "/v1/tenants/t1"]
    assert created and created[0]["scope"] == "*"  # criacao so com escopo admin do servico
    res = await last_result(rig, tool("rt_env_exec", {"command": "echo oi"}))
    assert res["result"]["exit_code"] == 0 and "echo oi" in res["result"]["stdout"]
    call = next(c for c in rig.ext.rm_calls if c["path"].endswith("workspace:exec"))
    assert call["scope"] == "t1" and call["body"]["command"] == "echo oi"


async def test_env_exec_failure_has_hint_and_stopped_workspace_is_refused(rig):
    rig.ext.rm_exec_exit = 2
    res = await last_result(rig, tool("rt_env_exec", {"command": "false"}))
    assert res["result"]["exit_code"] == 2 and "hint" in res["result"]
    res = await last_result(rig, tool("rt_env_exec", {"command": "  "}))
    assert res["error"]["code"] == "invalid_args"
    await last_result(rig, tool("rt_env_stop", {}), approve="approve")
    res = await last_result(rig, tool("rt_env_exec", {"command": "ls"}))
    assert not res["ok"]


def test_rm_tenant_id_is_valid_for_the_runtime_manager():
    import re

    from broker.env import rm_tenant
    t = rm_tenant("7b1f2c3d-aaaa-bbbb-cccc-1234567890ab")
    assert re.fullmatch(r"[a-z][a-z0-9]{3,19}", t) and t == "t7b1f2c3daaaa"


async def test_default_is_full_freedom_no_confirmation_card(rig):
    """Padrao do produto: sem confirm_actions o agente age direto (confirmar e papo da conversa)."""
    ev = await rig.send(rig.body(message=tool("rt_env_expose", {"app": "livre", "port": 4000}) + "[[echo]]",
                                 limits={"confirm_actions": False}))
    res = json.loads(ev[-1].data["text"])
    assert res["ok"], res
    assert not [e for e in ev if e.type == "approval_required"]
    assert rig.ext.exposures["t1"][-1]["app"] == "livre"


async def test_env_exec_injects_named_secrets_as_environment_and_never_leaks_them(rig):
    res = await last_result(rig, tool("rt_env_exec", {
        "command": "python pix.py", "secrets": ["MERCADO_PAGO_TOKEN"]}))
    assert res["ok"], res
    call = next(c for c in rig.ext.rm_calls if c["path"].endswith("workspace:exec"))
    assert call["body"]["env"]["MERCADO_PAGO_TOKEN"] == S_MP
    assert S_MP not in json.dumps(res)
    res = await last_result(rig, tool("rt_env_exec", {"command": "x", "secrets": ["NAO_EXISTE"]}))
    assert res["error"]["code"] == "invalid_args"


async def test_sql_write_without_table_list_allows_any_table_but_never_ddl(rig):
    for stmt, ok in (("INSERT INTO qualquer (a) VALUES (1)", True), ("DROP TABLE pedidos", False)):
        res = await last_result(rig, tool("rt_sql_write", {"statement": stmt}),
                                limits={"write_tables": [], "confirm_actions": False})
        if ok:
            assert res["ok"] or res["error"]["code"] == "tool_error", res
        else:
            assert res["error"]["code"] == "guardrail"


def _with_attachment(rig, name=b"planilha.xlsx", data=b"XLSXBYTES", kind="doc"):
    ref = "tenants/t1/chats/attachments/x/" + name.decode()
    rig.backend.attachment_files[ref] = data
    return {"extra": {"attachments": [{"kind": kind, "name": name.decode(), "storage_path": ref,
                                       "content_type": "application/octet-stream"}]}}


async def test_executor_reads_any_attachment_as_input_file(rig):
    kw = _with_attachment(rig)
    res = await last_result(rig, tool("rt_executor_run", {
        "script": "print(open('/in/planilha.xlsx','rb').read())", "attachments": ["planilha.xlsx"]}), **kw)
    assert res["ok"], res
    call = next(c for c in rig.ext.rm_calls if c["path"] == "/v1/executor/runs")
    import base64 as b64
    assert b64.b64decode(call["body"]["inputs"]["planilha.xlsx"]) == b"XLSXBYTES"
    res = await last_result(rig, tool("rt_executor_run", {"script": "x", "attachments": ["outro.bin"]}), **kw)
    assert res["error"]["code"] == "invalid_args" and "planilha.xlsx" in res["error"]["message"]


async def test_attachment_goes_to_workspace_and_result_file_comes_back_as_artifact(rig):
    kw = _with_attachment(rig, b"video.mp4", b"MP4DATA", "video")
    res = await last_result(rig, tool("rt_env_upload", {"attachment": "video.mp4"}), **kw)
    assert res["result"] == {"path": "/workspace/inbox/video.mp4", "size": 7}
    assert rig.ext.rm_files["/workspace/inbox/video.mp4"] == b"MP4DATA"
    rig.ext.rm_files["/workspace/out/resultado.xlsx"] = b"EDITADO"
    res = await last_result(rig, tool("rt_env_download", {"path": "/workspace/out/resultado.xlsx"}))
    art = res["result"]["artifact"]
    assert art["title"] == "resultado.xlsx" and rig.backend.files[art["artifact_id"]] == b"EDITADO"
    res = await last_result(rig, tool("rt_env_download", {"path": "/workspace/nao-existe"}))
    assert not res["ok"]


def test_run_request_accepts_any_attachment_kind():
    import json
    import pathlib

    import jsonschema
    schema = json.loads(pathlib.Path("contracts/v1/run_request.schema.json").read_text(encoding="utf-8"))
    att = schema["properties"]["attachments"]["items"]
    for kind in ("image", "audio", "video", "file", "pdf"):
        jsonschema.validate({"kind": kind, "name": "a", "storage_path": "x"}, att)
