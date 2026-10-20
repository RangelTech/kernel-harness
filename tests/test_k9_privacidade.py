"""K-9: cliente externo nao ve memoria/session_search e tem perfil proprio por conversa."""
from __future__ import annotations

from pathlib import Path

import yaml

from profiles import profile_config as pc

M = {"tools": [], "environment": {"enabled": False}}


def test_external_profile_id_is_per_contact_and_distinct_from_shared():
    a = pc.profile_id("t1", "tpl", None, "ext-i1-contato-A")
    b = pc.profile_id("t1", "tpl", None, "ext-i1-contato-B")
    c = pc.profile_id("t2", "tpl", None, "ext-i1-contato-A")
    shared = pc.profile_id("t1", "tpl", None)
    assert len({a, b, c, shared}) == 4
    assert a.startswith("e-") and shared.startswith("s-")
    assert a == pc.profile_id("t1", "tpl", None, "ext-i1-contato-A")  # deterministico


def test_external_config_has_no_memory_nor_session_search_and_no_learning():
    cfg = pc.build_config("t1", "tpl", None, M, pc.Paths(system_skills="/s"), external=True)
    ts = cfg["platform_toolsets"]["api_server"]
    assert "memory" not in ts and "session_search" not in ts
    assert {"memory", "session_search"} <= set(cfg["agent"]["disabled_toolsets"])
    assert cfg["skills"]["creation_nudge_interval"] == 0
    assert cfg["auxiliary"]["background_review"]["enabled"] is False


def test_member_config_keeps_memory_and_session_search():
    cfg = pc.build_config("t1", "tpl", "u1", M, pc.Paths(system_skills="/s"))
    ts = cfg["platform_toolsets"]["api_server"]
    assert "memory" in ts and "session_search" in ts


def test_external_ignores_user_skills_dir():
    cfg = pc.build_config("t1", "tpl", "u1", M, pc.Paths(system_skills="/s"), external=True)
    assert not any("/users/" in d or d.endswith("/u1/skills") for d in cfg["skills"]["external_dirs"])


def test_write_profile_external_isolated_dirs(tmp_path: Path):
    ra = pc.write_profile(tmp_path, "t1", "tpl", None, M, "m" * 32, pc.Paths(), contact="ext-i1-A")
    rb = pc.write_profile(tmp_path, "t1", "tpl", None, M, "m" * 32, pc.Paths(), contact="ext-i1-B")
    assert ra["path"] != rb["path"]
    cfg = yaml.safe_load((Path(ra["path"]) / "config.yaml").read_text(encoding="utf-8"))
    assert "session_search" not in cfg["platform_toolsets"]["api_server"]
    assert ra["api_key"] != rb["api_key"]
    # nenhum perfil 's-' (compartilhado) foi criado para o externo
    assert not [d for d in tmp_path.iterdir() if d.name.startswith("s-")]
