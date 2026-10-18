"""Fumaca do compose de dev: POST /v1/runs e imprime o SSE ate o fim."""
import sys
import time
import uuid

import httpx

sys.path.insert(0, ".")
from harness.security import sign_run_token  # noqa: E402

BASE = "http://localhost:8080"
rid = "smoke-" + uuid.uuid4().hex[:8]
body = {
    "contract_version": 1, "run_id": rid, "idempotency_key": rid, "tenant_id": "dev",
    "user_id": "u1", "actor": {"type": "member", "name": "Dev"}, "chat_id": "c1", "channel": "web",
    "template": {"id": "tpl", "version_id": "v1", "kind": "skills"},
    "message": sys.argv[1] if len(sys.argv) > 1 else "ola, tudo bem?",
    "skills": {"tenant_package": {"sha256": "x", "files": [
        {"path": "AGENTE.md", "content": "Voce e um agente de teste."}]}},
    "manifest": {"tools": ["rt_echo"]}, "credentials": {},
}
body["run_token"] = sign_run_token(
    {"run_id": rid, "tenant_id": "dev", "user_id": "u1", "exp": time.time() + 600},
    "dev-run-token-secret")
with httpx.stream("POST", f"{BASE}/v1/runs", json=body, timeout=120,
                  headers={"Authorization": "Bearer dev-service-token"}) as r:
    print("HTTP", r.status_code)
    for line in r.iter_lines():
        if line:
            print(line)
            if line.startswith("data:") and '"code"' in line:
                pass
    ok = r.status_code == 200
sys.exit(0 if ok else 1)
