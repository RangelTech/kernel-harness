"""LLM falso deterministico OpenAI-compativel (stdlib). Evolucao do poc/fake_llm.py.
Comandos na ULTIMA mensagem do usuario (uma ou mais linhas):
  CALL:<tool>|<json-args>   -> forca tool_call de QUALQUER nome (mesmo nao oferecido) ; k-esimo CALL na k-esima volta
  SLEEP:<seg>               -> atrasa a resposta
  BIG:<n>                   -> responde com ~n caracteres (infla contexto)
Responde: N_MSGS=<n>|LAST=<ultimo user>|RESULTS=<resultados das tools desta volta>
Loga em /logs/requests.jsonl: tools oferecidas, resultados de tool integrais (<=6000 chars), hash do system prompt.
Env: FAKE_DELAY_MS (latencia base), FAKE_LOG_SYS=1 (salva system prompt por hash em /logs/sys/)."""
import json, os, re, time, uuid, hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
LOG = "/logs/requests.jsonl"; os.makedirs("/logs/sys", exist_ok=True)
BASE_DELAY = float(os.environ.get("FAKE_DELAY_MS", "0")) / 1000.0
def txt(c):
    if isinstance(c, list): return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return c or ""
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def _send(self, code, obj):
        b = json.dumps(obj).encode(); self.send_response(code)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            return self._send(200, {"object": "list", "data": [{"id": "fake-model", "object": "model", "context_length": 128000}]})
        self._send(200, {"ok": True})
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); body = json.loads(self.rfile.read(n) or b"{}")
        msgs = body.get("messages", [])
        if not msgs and not body.get("tools"):   # nao e chat: loga (ex.: backend firecrawl falso) e responde generico
            with open(LOG, "a") as f:
                f.write(json.dumps({"t": time.time(), "path": self.path, "non_chat": True, "url": body.get("url") or body.get("urls"), "body_keys": sorted(body)[:10]}) + "\n")
            return self._send(200, {"success": True, "data": {"markdown": "FAKE-SCRAPE", "metadata": {}}, "ok": True})
        last_u = max([i for i, m in enumerate(msgs) if m.get("role") == "user"], default=-1)
        last = txt(msgs[last_u].get("content")) if last_u >= 0 else ""
        results = [txt(m.get("content")) for m in msgs[last_u + 1:] if m.get("role") == "tool"]
        sysmsg = " ".join(txt(m.get("content")) for m in msgs if m.get("role") == "system")
        sh = hashlib.md5(sysmsg.encode()).hexdigest()[:10]
        if os.environ.get("FAKE_LOG_SYS") and not os.path.exists(f"/logs/sys/{sh}.txt"):
            open(f"/logs/sys/{sh}.txt", "w").write(sysmsg)
        has_image = any(isinstance(m.get("content"), list) and any(isinstance(x, dict) and x.get("type") in ("image_url", "input_image", "image") for x in m["content"]) for m in msgs)
        users_all = [txt(m.get("content"))[:120] for m in msgs if m.get("role") == "user"]
        calls = re.findall(r"CALL:([A-Za-z0-9_\-:.]+)\|(.*)", last)
        with open(LOG, "a") as f:
            f.write(json.dumps({"t": time.time(), "n_msgs": len(msgs), "roles": [m.get("role") for m in msgs][-12:], "last_user": last[:300],
                "tools_offered": sorted(t.get("function", {}).get("name") for t in body.get("tools", [])), "sys_len": len(sysmsg), "sys_hash": sh,
                "tool_results": [r[:6000] for r in results], "stream": body.get("stream"), "model": body.get("model"), "has_image": has_image, "path": self.path}) + "\n")
        PT = max(10, len(json.dumps(msgs)) // 4)
        d = BASE_DELAY
        m = re.search(r"SLEEP:([0-9.]+)", last)
        if m: d += float(m.group(1))
        if d: time.sleep(d)
        tool_call = None
        k = len(results)
        if k < len(calls):
            name, args = calls[k]
            try: json.loads(args)
            except Exception: args = "{}"
            tool_call = {"id": "call_" + uuid.uuid4().hex[:8], "type": "function", "function": {"name": name, "arguments": args}}
        if tool_call: msg = {"role": "assistant", "content": None, "tool_calls": [tool_call]}; fin = "tool_calls"
        else:
            big = re.search(r"BIG:([0-9]+)", last)
            content = f"N_MSGS={len(msgs)}|USERS={' // '.join(users_all)[:500]}|RESULTS={' || '.join(r[:4000] for r in results)}"
            if big: content += " " + ("lorem " * (int(big.group(1)) // 6))
            msg = {"role": "assistant", "content": content}; fin = "stop"
        cid = "chatcmpl-" + uuid.uuid4().hex[:8]
        if body.get("stream"):
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Connection", "close"); self.end_headers()
            def ev(x): self.wfile.write(("data: " + json.dumps(x) + "\n\n").encode()); self.wfile.flush()
            delta = {"role": "assistant"}
            if msg.get("content"): delta["content"] = msg["content"]
            if tool_call: delta["tool_calls"] = [dict(tool_call, index=0)]
            ev({"id": cid, "object": "chat.completion.chunk", "model": "fake-model", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]})
            ev({"id": cid, "object": "chat.completion.chunk", "model": "fake-model", "choices": [{"index": 0, "delta": {}, "finish_reason": fin}], "usage": {"prompt_tokens": PT, "completion_tokens": 5, "total_tokens": PT + 5}})
            self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush(); self.close_connection = True
        else:
            self._send(200, {"id": cid, "object": "chat.completion", "model": "fake-model", "choices": [{"index": 0, "message": msg, "finish_reason": fin}], "usage": {"prompt_tokens": PT, "completion_tokens": 5, "total_tokens": PT + 5}})
ThreadingHTTPServer(("0.0.0.0", 9000), H).serve_forever()
