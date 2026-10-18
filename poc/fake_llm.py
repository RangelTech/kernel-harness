"""LLM falso OpenAI-compativel, deterministico, so stdlib. Dados sinteticos.

Diretivas na ULTIMA mensagem de usuario (uma por linha):
    CALL:<tool>:<json-args>   emite uma tool_call (varias linhas = varias chamadas em paralelo)
    SLOW <n>                  dorme n segundos antes de responder
    SAY:<texto>               resposta final fixa
Sem diretiva responde "N_MSGS=<n>|USERS=<textos de usuario>|TOOLRESULTS=<...>".

Usage reportado e deterministico (para H5): prompt_tokens = 1000 * (n. de msgs de usuario no
contexto) + 14 * n. de msgs tool; completion_tokens = 7.  Assim da para distinguir "delta do turno"
de "acumulado da sessao".

Cada requisicao e gravada em /logs/requests.jsonl (inclui o header Authorization COMPLETO: so
usar chaves sinteticas aqui).
"""
import json
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = "/logs/requests.jsonl"


def txt(c):
    if isinstance(c, list):
        return " ".join(p.get("text", "") if p.get("type") == "text" else f"[{p.get('type')}]" for p in c if isinstance(p, dict))
    return c or ""


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        with open(LOG, "a") as f:
            f.write(json.dumps({"t": time.time(), "method": "GET", "path": self.path, "auth": self.headers.get("Authorization")}) + "\n")
        if self.path.rstrip("/").endswith("/models"):
            return self._send(200, {"object": "list", "data": [{"id": "fake-model", "object": "model", "context_length": 128000}]})
        self._send(200, {"ok": True})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        msgs = body.get("messages", [])
        users = [txt(m.get("content")) for m in msgs if m.get("role") == "user"]
        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        last_user_idx = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=-1)
        answered = any(m.get("role") == "tool" for m in msgs[last_user_idx + 1:])
        last = users[-1] if users else ""
        last_raw = msgs[last_user_idx].get("content") if last_user_idx >= 0 else None
        part_types = [p.get("type") for p in last_raw if isinstance(p, dict)] if isinstance(last_raw, list) else []
        sys_head = next((txt(m.get("content"))[:200] for m in msgs if m.get("role") in ("system", "developer")), "")
        sys_full = next((txt(m.get("content")) for m in msgs if m.get("role") in ("system", "developer")), "")
        with open(LOG, "a") as f:
            f.write(json.dumps({
                "t": time.time(), "method": "POST", "path": self.path, "auth": self.headers.get("Authorization"),
                "n_msgs": len(msgs), "roles": [m.get("role") for m in msgs], "users": users,
                "last_part_types": part_types,
                "tools_offered": sorted(t.get("function", {}).get("name") for t in body.get("tools", []) or []),
                "stream": body.get("stream"), "sys_head": sys_head, "sys_len": len(sys_full), "sys": sys_full,
                "sys_skill_names": [ln.strip()[:80] for ln in sys_full.splitlines() if ln.strip().startswith("- ") and ":" in ln][:60],
            }) + "\n")
        users_n = len(users)
        pt = max(10, 1000 * users_n + 14 * len(tool_msgs))
        calls = []
        if not answered:
            for line in last.splitlines():
                line = line.strip()
                if line.startswith("CALL:"):
                    rest = line[5:]
                    name, _, js = rest.partition(":")
                    calls.append({"id": "call_" + uuid.uuid4().hex[:8], "type": "function",
                                  "function": {"name": name.strip(), "arguments": js.strip() or "{}"}})
        if "SLOW" in last:
            try:
                time.sleep(float(last.split("SLOW", 1)[1].split()[0]))
            except Exception:
                time.sleep(5)
        if calls:
            msg = {"role": "assistant", "content": None, "tool_calls": calls}
            fin = "tool_calls"
        else:
            say = next((ln.strip()[4:] for ln in last.splitlines() if ln.strip().startswith("SAY:")), None)
            tr = " || ".join(txt(m.get("content"))[:400] for m in tool_msgs[-3:])
            content = say if say is not None else f"N_MSGS={len(msgs)}|USERS={' // '.join(users)[:600]}" + (f"|TOOLRESULTS={tr}" if tr else "")
            msg = {"role": "assistant", "content": content}
            fin = "stop"
        cid = "chatcmpl-" + uuid.uuid4().hex[:8]
        usage = {"prompt_tokens": pt, "completion_tokens": 7, "total_tokens": pt + 7}
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()

            def ev(d):
                self.wfile.write(("data: " + json.dumps(d) + "\n\n").encode())
                self.wfile.flush()

            delta = {"role": "assistant"}
            if msg.get("content"):
                delta["content"] = msg["content"]
            if calls:
                delta["tool_calls"] = [dict(c, index=i) for i, c in enumerate(calls)]
            ev({"id": cid, "object": "chat.completion.chunk", "model": "fake-model", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]})
            ev({"id": cid, "object": "chat.completion.chunk", "model": "fake-model", "choices": [{"index": 0, "delta": {}, "finish_reason": fin}], "usage": usage})
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            self.close_connection = True
        else:
            self._send(200, {"id": cid, "object": "chat.completion", "model": "fake-model",
                             "choices": [{"index": 0, "message": msg, "finish_reason": fin}], "usage": usage})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 9000), H).serve_forever()
