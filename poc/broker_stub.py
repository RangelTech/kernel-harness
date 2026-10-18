"""Broker de mentira para POC H7: registra cada chamada do plugin rt-broker e responde JSON.
Regras: token errado -> 401; session_id vazio -> 400; session_id que comeca com 'deny-' -> 403.
Grava em /logs/broker.jsonl (sem o token)."""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ.get("RT_BROKER_TOKEN", "pooltoken-0123456789")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        auth_ok = self.headers.get("Authorization") == f"Bearer {TOKEN}"
        rec = {"t": time.time(), "path": self.path, "auth_ok": auth_ok, "body": body}
        with open("/logs/broker.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")
        if not auth_ok:
            return self._send(401, {"error": "bad_token"})
        sid = body.get("session_id") or ""
        if not sid:
            return self._send(400, {"error": "no_session_id"})
        if sid.startswith("deny-"):
            return self._send(403, {"error": "session_not_active"})
        tool = body.get("tool")
        if tool == "rt_echo":
            return self._send(200, {"ok": True, "echo": (body.get("args") or {}).get("text"), "session_id": sid})
        self._send(200, {"ok": True, "tool": tool, "session_id": sid, "args": body.get("args")})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8099), H).serve_forever()
