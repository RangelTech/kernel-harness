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
    assert _ext(_cfg([])) == ["/skills/system/core", "/skills/tenant/tpl/current",
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
