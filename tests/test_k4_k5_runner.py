from __future__ import annotations

import json

from qa.k4_k5_runner import Observation, Settings, build_body, report, sign_backend_run_token


def settings() -> Settings:
    return Settings("http://kernel.test", "service", "secret", "tpl", "v1", "model", "oi", 60)


def test_build_body_has_unique_bound_signed_token():
    body = build_body(settings(), tenant_id="qa-0", index=3)
    assert body["tenant_id"] == "qa-0"
    assert body["actor"]["type"] == "member"
    assert body["manifest"]["tools"] == []
    assert body["run_token"].count(".") == 1
    assert "secret" not in json.dumps(body)


def test_backend_token_is_deterministic_for_claims():
    claims = {"run_id": "r1", "tenant_id": "t1", "user_id": "u1", "exp": 123}
    assert sign_backend_run_token(claims, "s") == sign_backend_run_token(claims, "s")
    assert sign_backend_run_token(claims, "s") != sign_backend_run_token({**claims, "run_id": "r2"}, "s")


def test_report_calculates_success_and_first_event_percentiles():
    good = Observation("r1", "t", 2.0, 0.4, "done", "completed", None, 200)
    bad = Observation("r2", "t", 3.0, 1.2, "error", "interrupted", "interrupted", 200)
    result = report("soak", [good, bad], elapsed_s=3.0, args=type("A", (), {"concurrency": 2, "tenants": 1})())
    assert result["counts"] == {"submitted": 2, "completed": 1, "not_completed": 1}
    assert result["success_ratio"] == 0.5
    assert result["first_event_s"]["p95"] == 1.2


def test_work_event_ignores_the_immediate_start_step():
    from qa.k4_k5_runner import is_work_event
    start = ['{"kind": "queue", "label": "Iniciando", "status": "done"}']
    assert not is_work_event("step", start)
    assert is_work_event("step", ['{"kind": "tool", "label": "Consultando dados", "status": "start"}'])
    assert is_work_event("token", ['{"text": "oi"}'])
    assert is_work_event("error", ['{"code": "failed"}'])


def test_report_has_first_work_percentiles_and_body_carries_tenant_package_files():
    obs = Observation("r1", "t", 5.0, 0.2, "done", "completed", None, 200, 4.1)
    result = report("soak", [obs], elapsed_s=5.0, args=type("A", (), {"concurrency": 1, "tenants": 1})())
    assert result["first_work_s"]["p95"] == 4.1
    body = build_body(settings(), tenant_id="qa-0", index=0)
    assert body["skills"]["tenant_package"]["files"][0]["path"] == "AGENTE.md"


def test_body_can_point_the_run_to_a_fake_gateway():
    s = Settings("http://kernel.test", "service", "secret", "tpl", "v1", "model", "oi", 60,
                 gateway_url="http://fake:9999/v1")
    body = build_body(s, tenant_id="qa-0", index=0)
    assert body["model"]["gateway"]["base_url"] == "http://fake:9999/v1"
    assert "gateway" not in build_body(settings(), tenant_id="qa-0", index=0)["model"]
