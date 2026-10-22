"""Gerador de perfis (profiles/profile_config.py): skills de sistema por grupo e chave do gateway no .env."""
from __future__ import annotations

from pathlib import Path

import yaml

from profiles import profile_config as pc


def _cfg(tools, env=False):
    environment = {"enabled": env}
    if env:
        environment.update(ssh_host="ws-t1", ssh_key_path="/keys/t1")
    return pc.build_config("t1", "tpl", "u1", {"tools": tools, "environment": environment},
                           pc.Paths(system_skills="/skills/system"))


def _ext(cfg):
    return cfg["skills"]["external_dirs"]


def test_system_skill_groups_follow_manifest_tools():
    assert _ext(_cfg([])) == ["/skills/system/core", "/skills/system/exemplos",
                              "/skills/tenant/tpl/current",
                              "/srv/hermes/users/t1/u1/skills"]
    e = _ext(_cfg(["rt_pix_create", "rt_sql_read"]))
    assert "/skills/system/pix" in e and "/skills/system/dados" in e
    assert "/skills/system/email" not in e and "/skills/system/ambiente" not in e


def test_capability_ids_and_environment_enable_groups():
    assert "/skills/system/ambiente" in _ext(_cfg(["rt_env_manage"]))
    assert "/skills/system/ambiente" in _ext(_cfg([], env=True))
    assert "/skills/system/rotinas" in _ext(_cfg(["rt_job_schedule"]))
    assert "/skills/system/analise" in _ext(_cfg(["rt_executor_run"]))


def test_model_uses_key_env_and_profile_env_holds_the_key(tmp_path: Path):
    r = pc.write_profile(tmp_path, "t1", "tpl", "u1", {"tools": []}, "m" * 32, pc.Paths(),
                         llm_api_key="sk-virtual-abc")
    root = Path(r["path"])
    cfg = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["model"]["key_env"] == "OPENAI_API_KEY"
    env = (root / ".env").read_text(encoding="utf-8")
    assert f"API_SERVER_KEY={r['api_key']}" in env and "OPENAI_API_KEY=sk-virtual-abc" in env
    assert "sk-virtual-abc" not in (root / "config.yaml").read_text(encoding="utf-8")
    # trocar a chave reescreve o .env sem mexer no resto
    pc.write_profile(tmp_path, "t1", "tpl", "u1", {"tools": []}, "m" * 32, pc.Paths(),
                     llm_api_key="sk-virtual-xyz")
    assert "OPENAI_API_KEY=sk-virtual-xyz" in (root / ".env").read_text(encoding="utf-8")


def test_environment_enabled_without_ssh_uses_runtime_manager_not_terminal():
    cfg = pc.build_config("t1", "tpl", "u1", {"tools": [], "environment": {"enabled": True}},
                          pc.Paths(system_skills="/skills/system"))
    assert "/skills/system/ambiente" in _ext(cfg)
    ts = cfg["platform_toolsets"]["api_server"]
    assert "terminal" not in ts and "file" not in ts
    assert {"terminal", "code_execution", "file"} <= set(cfg["agent"]["disabled_toolsets"])
    assert cfg["terminal"]["ssh_host"] == pc.NO_ENV_SSH_HOST


def test_ssh_environment_half_configured_is_rejected():
    import pytest
    with pytest.raises(pc.ConfigError):
        pc.build_config("t1", "tpl", "u1",
                        {"tools": [], "environment": {"enabled": True, "ssh_host": "h"}},
                        pc.Paths())


def test_manifest_toolsets_reach_the_profile_and_forbidden_ones_are_refused():
    import pytest
    cfg = pc.build_config("t1", "tpl", "u1", {"tools": [], "toolsets": ["web"]}, pc.Paths())
    assert "web" in cfg["platform_toolsets"]["api_server"]
    with pytest.raises(pc.ConfigError):
        pc.build_config("t1", "tpl", "u1", {"tools": [], "toolsets": ["terminal"]}, pc.Paths())


def test_model_failures_surface_fast_to_the_kernel():
    cfg = pc.build_config("t1", "tpl", "u1", {"tools": []}, pc.Paths())
    assert cfg["agent"]["api_max_retries"] == 1
    assert cfg["agent"]["auto_recovery_cycles"] == 0


def test_expand_tools_adds_env_tools_when_environment_is_on():
    from broker.catalog import environment_enabled, expand_tools
    assert "rt_env_exec" not in expand_tools([])
    on = expand_tools([], environment_enabled({"environment": {"enabled": True}}))
    assert {"rt_env_exec", "rt_env_start", "rt_env_status"} <= set(on)
    assert not environment_enabled({"environment": {"enabled": False}})
    assert not environment_enabled({})


def test_unused_bundled_plugins_are_disabled_unless_the_toolset_is_on():
    base = pc.build_config("t1", "tpl", "u1", {"tools": []}, pc.Paths())["plugins"]["disabled"]
    assert "browser/browser_use" in base and "dashboard_auth/basic" in base
    assert "web/exa" in base and "image_gen/fal" in base
    web = pc.build_config("t1", "tpl", "u1", {"tools": [], "toolsets": ["web", "image_gen"]},
                          pc.Paths())["plugins"]["disabled"]
    assert "web/exa" not in web and "image_gen/fal" not in web
    assert "browser/browser_use" in web  # browser nunca
