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
