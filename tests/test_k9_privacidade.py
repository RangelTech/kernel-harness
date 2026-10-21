"""K-9: cliente externo nao ve memoria/session_search e tem perfil proprio por conversa."""
from __future__ import annotations

from pathlib import Path

import yaml

from harness.profiles import profile_name
from profiles import profile_config as pc
from tests.conftest import final, text_of

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


# ------------------------------------------------------------------------------------------------
# Ensaio E2E (K-9, pendencia de anexos/10/K-fechamento.md): canario atraves do caminho real
# HTTP /v1/runs + SSE (kernel -> dispatcher -> ProfileManager -> Hermes falso), nao so a funcao pura
# de config. Prova que dois contatos externos do MESMO template/tenant nunca compartilham perfil nem
# sessao, e que o perfil "shared" do template nunca e usado para contato externo.
async def test_k9_e2e_canario_nao_atravessa_contatos_externos_mesmo_tenant_e_template(rig):
    canary = "CANARY-AAA-7f21"
    a = await rig.send(rig.body(tenant="t1", template="tpl1", user=None, actor_type="external",
                                chat="wa-contato-A", message=f"meu codinome e {canary}"))
    assert final(a).type == "done"
    b = await rig.send(rig.body(tenant="t1", template="tpl1", user=None, actor_type="external",
                                chat="wa-contato-B", message="qual meu codinome?"))
    assert final(b).type == "done"
    assert canary not in text_of(b)
    assert rig.hermes[0].sessions[next(
        k for k in rig.hermes[0].sessions if k[1].endswith("wa-contato-B"))].facts == {}

    profiles_used = {req["profile"] for req in rig.hermes[0].requests}
    assert len(profiles_used) == 2, profiles_used
    shared = profile_name("t1", "tpl1", None)
    assert shared not in profiles_used  # K-9: externo nunca cai no perfil "shared" do template


async def test_k9_e2e_canario_nao_atravessa_contatos_externos_em_outro_tenant(rig):
    canary = "CANARY-BBB-9c40"
    await rig.send(rig.body(tenant="t1", template="tpl1", user=None, actor_type="external",
                            chat="wa-contato-C", message=f"meu codinome e {canary}"))
    out = await rig.send(rig.body(tenant="t2", template="tpl1", user=None, actor_type="external",
                                  chat="wa-contato-D", message="qual meu codinome?"))
    assert canary not in text_of(out)

    reqs = [r for r in rig.hermes[0].requests if r["body"].get("session_id", "").startswith(
        ("t1:", "t2:"))]
    profiles_by_tenant = {r["profile"] for r in reqs if r["body"]["session_id"].startswith("t1:")}
    profiles_by_tenant2 = {r["profile"] for r in reqs if r["body"]["session_id"].startswith("t2:")}
    assert profiles_by_tenant.isdisjoint(profiles_by_tenant2)


async def test_k9_e2e_perfil_shared_nunca_e_tocado_por_varios_contatos_externos(rig):
    for i in range(4):
        ev = await rig.send(rig.body(tenant="t1", template="tpl1", user=None, actor_type="external",
                                     chat=f"wa-contato-{i}", message="oi"))
        assert final(ev).type == "done"
    shared = profile_name("t1", "tpl1", None)
    profiles_used = {req["profile"] for req in rig.hermes[0].requests}
    assert len(profiles_used) == 4  # um perfil por contato
    assert shared not in profiles_used
