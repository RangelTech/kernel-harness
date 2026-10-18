"""Cliente minimo da API Hermes para as POCs/bateria (stdlib). Importar via sys.path (nao use -I)."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

TERMINAL = ("completed", "failed", "cancelled", "interrupted")
ACTIVE = ("queued", "started", "running", "stopping", "waiting_for_approval")


def call(port, key, method, path, body=None, headers=None, raw=False, timeout=60, host="127.0.0.1", data=None):
    h = {"Content-Type": "application/json"}
    if key:
        h["Authorization"] = f"Bearer {key}"
    h.update(headers or {})
    payload = data if data is not None else (json.dumps(body).encode() if body is not None else None)
    r = urllib.request.Request(f"http://{host}:{port}{path}", data=payload, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as x:
            d = x.read().decode("utf-8", "replace")
            return x.status, dict(x.headers), (d if raw else _maybe_json(d))
    except urllib.error.HTTPError as e:
        d = e.read().decode("utf-8", "replace")
        return e.code, dict(e.headers), (d if raw else _maybe_json(d))


def _maybe_json(d):
    s = d.strip()
    if s.startswith(("{", "[")):
        try:
            return json.loads(s)
        except Exception:
            return d
    return d


def parse_sse(text: str):
    """-> lista de dicts {id, event, data(json|str), comment}"""
    out = []
    cur = {}
    for line in text.splitlines():
        if line == "":
            if cur:
                out.append(cur)
                cur = {}
            continue
        if line.startswith(":"):
            cur.setdefault("comments", []).append(line[1:].strip())
            continue
        k, _, v = line.partition(":")
        v = v.lstrip(" ")
        if k == "data":
            cur["data"] = (cur.get("data", "") + "\n" + v) if "data" in cur else v
        else:
            cur[k] = v
    if cur:
        out.append(cur)
    for e in out:
        if "data" in e:
            try:
                e["data"] = json.loads(e["data"])
            except Exception:
                pass
    return out


def events(port, key, run_id, prefix="", last_event_id=None, timeout=60):
    hdr = {"Accept": "text/event-stream"}
    if last_event_id is not None:
        hdr["Last-Event-ID"] = str(last_event_id)
    st, h, txt = call(port, key, "GET", f"{prefix}/v1/runs/{run_id}/events", headers=hdr, raw=True, timeout=timeout)
    return st, h, parse_sse(txt) if isinstance(txt, str) else txt


def start(port, key, text, session_id=None, idem=None, prefix="", extra=None, headers=None):
    body = {"input": text}
    if session_id:
        body["session_id"] = session_id
    body.update(extra or {})
    h = dict(headers or {})
    if idem:
        h["Idempotency-Key"] = idem
    return call(port, key, "POST", f"{prefix}/v1/runs", body, h)


def wait(port, key, run_id, prefix="", timeout=120, poll=0.4):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        s, _, p = call(port, key, "GET", f"{prefix}/v1/runs/{run_id}")
        last = p
        if isinstance(p, dict) and p.get("status") in TERMINAL:
            return p
        time.sleep(poll)
    return {"timeout": True, "last": last}


def run(port, key, text, session_id=None, idem=None, prefix="", extra=None, timeout=120, headers=None):
    """Inicia e espera o estado terminal (polling). -> (http, resp_inicio, run_final)"""
    st, h, d = start(port, key, text, session_id, idem, prefix, extra, headers)
    if st >= 300 or not isinstance(d, dict) or "run_id" not in d:
        return st, d, None
    return st, d, wait(port, key, d["run_id"], prefix, timeout)


def run_events(port, key, text, session_id=None, prefix="", extra=None, timeout=120, headers=None):
    """Inicia, espera terminar e devolve (run_id, eventos SSE completos, run final)."""
    st, h, d = start(port, key, text, session_id, None, prefix, extra, headers)
    if st >= 300:
        return None, d, None
    fin = wait(port, key, d["run_id"], prefix, timeout)
    _, _, ev = events(port, key, d["run_id"], prefix, timeout=30)
    return d["run_id"], ev, fin
