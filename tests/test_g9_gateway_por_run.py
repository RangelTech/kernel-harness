"""G-9: o motor manda `model.gateway`; o perfil do Hermes usa essa base_url e essa chave (restrita ao combo)."""
from __future__ import annotations

from pathlib import Path

import yaml

from harness.config import Settings
from harness.profiles import RealProfileGenerator

M = {"tools": [], "environment": {"enabled": False}}


def _settings(tmp: Path) -> Settings:
    return Settings(hermes_master_secret="m" * 32, profiles_root=str(tmp / "p"),
                    tenant_skills_root=str(tmp / "t"), users_root=str(tmp / "u"),
                    litellm_url="http://litellm:4000", llm_worker_key="legacy-pool-key")


def _gen(tmp: Path, model: dict) -> tuple[dict, str]:
    g = RealProfileGenerator(_settings(tmp))
    r = g.generate(tenant_id="t1", template_id="tpl", user_id="u1", manifest=M, model=model)
    home = Path(r["home"])
    return yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8")), (home / ".env").read_text(encoding="utf-8")


def test_gateway_do_run_vira_base_url_e_chave_do_perfil(tmp_path):
    cfg, env = _gen(tmp_path, {"alias": "main", "gateway": {"base_url": "http://llm-gateway:20128/v1/", "api_key": "sk-combo-main"}})
    assert cfg["model"]["base_url"] == "http://llm-gateway:20128/v1"
    assert cfg["model"]["default"] == "main"
    assert "OPENAI_API_KEY=sk-combo-main" in env
    assert "legacy-pool-key" not in env


def test_sem_gateway_cai_no_env_legado(tmp_path):
    cfg, env = _gen(tmp_path, {"alias": "ninerouter-haiku"})
    assert cfg["model"]["base_url"] == "http://litellm:4000/v1"
    assert "OPENAI_API_KEY=legacy-pool-key" in env
