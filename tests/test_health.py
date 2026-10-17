from fastapi.testclient import TestClient

from harness.main import app


def test_health():
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_version():
    r = TestClient(app).get("/version")
    assert r.status_code == 200
    assert "version" in r.json()
