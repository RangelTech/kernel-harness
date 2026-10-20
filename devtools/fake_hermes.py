"""FAKE Hermes (API server headless) para testes offline e compose de dev.

Imita o contrato real medido nas POCs (anexos/04/hermes-agent-fonte.md secao 2):
  POST /p/<perfil>/v1/runs            202 + run_id; Idempotency-Key; 429 acima do limite
  GET  /p/<perfil>/v1/runs/<id>/events SSE com `seq`, replay por Last-Event-ID
  POST .../stop | approval | steer
  sessoes por (perfil, session_id) que sobrevivem entre runs (o "state.db"); usage ACUMULADO.

O comportamento e dirigido por diretivas no texto de entrada, na ordem em que aparecem:
  [[say:texto]]  [[think:texto]]  [[tool:rt_x {json}]]  [[skill:nome]]  [[sleep:seg]]
  [[usage:in,out]]  [[proposal:nome|conteudo com \\n]]  [[memproposal:texto]]
  [[reason:texto]]  [[sub:objetivo|id|tarefas]]  [[subdone:id|objetivo]]
  [[approvalreq]]  [[listskills]]  [[interrupted]]  [[partial]]  [[fail]]  [[fail429:N]]  [[empty]]
Sem diretivas: responde `ok[n] <inicio da mensagem>` (n = turno na sessao).
Regras de memoria da sessao: "meu codinome e X" guarda; "qual meu codinome" recupera.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

_DIRECTIVE = re.compile(r"\[\[(\w+)(?::(.*?))?\]\]", re.S)
TERMINALS = {"run.completed", "run.failed", "run.cancelled", "run.interrupted"}


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


@dataclass
class Session:
    messages: list[dict] = field(default_factory=list)
    facts: dict = field(default_factory=dict)
    tot_in: int = 0
    tot_out: int = 0
    compressions: int = 0
    rehydrated: bool = False
    turns: int = 0


@dataclass
class FakeRun:
    id: str
    profile: str
    session_id: str
    text: str
    history: list[dict]
    events: list[tuple[int, str, dict]] = field(default_factory=list)
    new_event: asyncio.Event = field(default_factory=asyncio.Event)
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    approval: asyncio.Future | None = None
    steers: list[str] = field(default_factory=list)
    finished: bool = False
    task: asyncio.Task | None = None
    started: asyncio.Event = field(default_factory=asyncio.Event)
    last_tool_result: str = ""
    t0: float = 0.0
    t1: float = 0.0

    def emit(self, name: str, **data) -> None:
        seq = len(self.events) + 1
        self.events.append((seq, name, {"event": name, "seq": seq, **data}))
        self.new_event.set()
        if name in TERMINALS:
            self.finished = True


class FakeHermes:
    def __init__(self, profiles_root: str, *, broker_url: str = "", broker_token: str = "",
                 key: str = "", max_concurrent: int = 4, cumulative_usage: bool = True,
                 tokens_per_turn: int = 120, http: httpx.AsyncClient | None = None):
        self.profiles_root = Path(profiles_root)
        self.broker_url = broker_url
        self.broker_token = broker_token
        self.key = key
        self.max_concurrent = max_concurrent
        self.cumulative = cumulative_usage
        self.tokens_per_turn = tokens_per_turn
        self.http = http or httpx.AsyncClient(timeout=200.0)
        self.runs: dict[str, FakeRun] = {}
        self.rate_limited: dict[str, int] = {}
        self.idem: dict[tuple[str, str], str] = {}
        self.sessions: dict[tuple[str, str], Session] = {}
        self.active = 0
        self.healthy = True
        self.requests: list[dict] = []  # corpos recebidos (inspecao em testes)
        self.writers: dict[str, set[str]] = {}  # perfil -> run ids (escritor unico)
        self.app = self._build()

    # ------------------------------------------------------------- admin
    def cancel_all(self) -> None:
        for r in self.runs.values():
            if r.task and not r.task.done():
                r.task.cancel()

    def session(self, profile: str, session_id: str) -> Session:
        return self.sessions.setdefault((profile, session_id), Session())

    # --------------------------------------------------------------- app
    def _auth(self, request: Request) -> bool:
        return not self.key or request.headers.get("authorization") == f"Bearer {self.key}"

    def _build(self) -> FastAPI:
        app = FastAPI(title="fake-hermes")
        me = self

        @app.get("/health")
        async def health():
            if not me.healthy:
                return JSONResponse({"status": "down"}, status_code=503)
            return {"status": "ok"}

        @app.post("/p/{profile}/v1/runs")
        async def start(profile: str, request: Request):
            if not me._auth(request):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            body = await request.json()
            me.requests.append({"profile": profile, "body": body,
                                "headers": dict(request.headers)})
            idem = request.headers.get("idempotency-key")
            if idem and (profile, idem) in me.idem:
                return JSONResponse({"run_id": me.idem[(profile, idem)], "status": "started"},
                                    status_code=202)
            if me.active >= me.max_concurrent:
                return JSONResponse({"error": "too many runs"}, status_code=429,
                                    headers={"Retry-After": "0.1"})
            rid = "hr_" + uuid.uuid4().hex[:12]
            run = FakeRun(rid, profile, body.get("session_id") or rid, body.get("input", ""),
                          body.get("conversation_history") or [])
            me.runs[rid] = run
            if idem:
                me.idem[(profile, idem)] = rid
            me.active += 1
            run.task = asyncio.create_task(me._execute(run))
            return JSONResponse({"run_id": rid, "status": "started"}, status_code=202)

        @app.get("/p/{profile}/v1/runs/{rid}/events")
        async def events(profile: str, rid: str, request: Request):
            run = me.runs.get(rid)
            if run is None or run.profile != profile:
                return JSONResponse({"error": "not found"}, status_code=404)
            last = int(request.headers.get("last-event-id") or 0)

            async def gen():
                idx = last
                while True:
                    while idx < len(run.events):
                        seq, name, data = run.events[idx]
                        idx += 1
                        yield f"id: {seq}\nevent: {name}\ndata: {json.dumps(data)}\n\n"
                        if name in TERMINALS:
                            return
                    run.new_event.clear()
                    if idx < len(run.events):
                        continue
                    try:
                        await asyncio.wait_for(run.new_event.wait(), 5.0)
                    except TimeoutError:
                        yield ": keepalive\n\n"

            return StreamingResponse(gen(), media_type="text/event-stream")

        @app.post("/p/{profile}/v1/runs/{rid}/stop")
        async def stop(profile: str, rid: str):
            run = me.runs.get(rid)
            if run:
                run.stop.set()
            return {"status": "stopping"}

        @app.post("/p/{profile}/v1/runs/{rid}/approval")
        async def approval(profile: str, rid: str, request: Request):
            run = me.runs.get(rid)
            body = await request.json()
            if run and run.approval and not run.approval.done():
                run.approval.set_result(body.get("choice"))
            return {"status": "ok"}

        @app.post("/p/{profile}/v1/runs/{rid}/steer")
        async def steer(profile: str, rid: str, request: Request):
            run = me.runs.get(rid)
            body = await request.json()
            if run:
                run.steers.append(body.get("input", ""))
            return {"status": "ok"}

        return app

    # ------------------------------------------------------ execucao
    async def _execute(self, run: FakeRun) -> None:
        run.t0 = time.monotonic()
        try:
            await self._run(run)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            run.emit("run.failed", error=f"fake: {type(exc).__name__}: {exc}")
        finally:
            run.t1 = time.monotonic()
            self.active -= 1

    def _usage(self, sess: Session, text: str, in_tok: int | None, out_tok: int | None,
               out_text: str) -> dict:
        i = in_tok if in_tok is not None else self.tokens_per_turn + len(text) // 4
        o = out_tok if out_tok is not None else max(8, len(out_text) // 4)
        sess.tot_in += i
        sess.tot_out += o
        if self.cumulative:
            return {"input_tokens": sess.tot_in, "output_tokens": sess.tot_out,
                    "total_tokens": sess.tot_in + sess.tot_out}
        return {"input_tokens": i, "output_tokens": o, "total_tokens": i + o}

    async def _interruptible(self, run: FakeRun, seconds: float) -> bool:
        """True se parou por `stop`."""
        try:
            await asyncio.wait_for(run.stop.wait(), seconds)
            return True
        except TimeoutError:
            return False

    async def _call_broker(self, run: FakeRun, tool: str, args: dict) -> str:
        if not self.broker_url:
            return json.dumps({"error": "broker_not_configured"})
        try:
            r = await self.http.post(
                self.broker_url, json={"session_id": run.session_id, "tool": tool, "args": args,
                                       "meta": {"profile": run.profile}},
                headers={"Authorization": f"Bearer {self.broker_token}"}, timeout=200.0)
        except httpx.HTTPError as exc:
            return json.dumps({"error": "broker_unreachable", "detail": type(exc).__name__})
        if r.status_code >= 400:
            return json.dumps({"error": f"broker_http_{r.status_code}", "broker": r.json()})
        return r.text

    def _list_skills(self, run: FakeRun) -> list[str]:
        cfg_path = self.profiles_root / run.profile / "config.yaml"
        names: list[str] = []
        if not cfg_path.exists():
            return names
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        for d in ((cfg.get("skills") or {}).get("external_dirs") or []):
            root = Path(d)
            if root.is_dir():
                names += sorted(p.parent.name for p in root.rglob("SKILL.md"))
        return names

    async def _run(self, run: FakeRun) -> None:  # noqa: C901
        sess = self.session(run.profile, run.session_id)
        run.started.set()
        text = run.text
        if text.strip() == "/compress":
            sess.compressions += 1
            run.emit("run.completed", output="Sessão compactada.",
                     usage=self._usage(sess, "", 50, 10, ""), runtime={"provider": "fake",
                                                                       "model": "fake"})
            return
        if not sess.messages and run.history:
            sess.messages = [dict(m) for m in run.history]
            sess.rehydrated = True
            for m in sess.messages:
                if (mm := re.search(r"meu codinome e (\w+)", _fold(m.get("content", "")))):
                    sess.facts["codinome"] = mm.group(1)
        sess.turns += 1
        sess.messages.append({"role": "user", "content": text})
        folded = _fold(text)
        if (m := re.search(r"meu codinome e (\w+)", folded)):
            sess.facts["codinome"] = m.group(1)

        parts: list[str] = []
        in_tok = out_tok = None
        directives = _DIRECTIVE.findall(text)
        if not directives:
            if "qual meu codinome" in folded:
                reply = (f"Seu codinome e {sess.facts['codinome']}." if "codinome" in sess.facts
                         else "Nao sei seu codinome.")
            else:
                reply = f"ok[{sess.turns}] {text[:40]}"
            directives = [("say", reply)]
        for name, arg in directives:
            if run.stop.is_set():
                run.emit("run.cancelled")
                return
            if name == "say":
                for chunk in re.findall(r".{1,12}", arg, re.S):
                    run.emit("message.delta", delta=chunk)
                    parts.append(chunk)
                    await asyncio.sleep(0)
            elif name == "think":
                run.emit("message.delta", delta=f"<think>{arg}</think>")
            elif name == "reason":
                run.emit("reasoning.available", text=arg or "")
            elif name == "sub":
                goal, _, rest = (arg or "").partition("|")
                sid, _, count = rest.partition("|")
                run.emit("subagent.start", goal=goal, subagent_id=sid or None,
                         delegation_id="d1", task_count=int(count) if count else None)
            elif name == "subdone":
                sid, _, goal = (arg or "").partition("|")
                run.emit("subagent.complete", goal=goal, subagent_id=sid or None,
                         delegation_id="d1", status="completed")
            elif name == "sleep":
                if await self._interruptible(run, float(arg or 1)):
                    run.emit("run.cancelled")
                    return
            elif name == "usage":
                a, _, b = (arg or "").partition(",")
                in_tok, out_tok = int(a or 0), int(b or 0)
            elif name == "skill":
                run.emit("tool.started", tool="skill_view", preview=json.dumps({"name": arg}))
                run.emit("tool.completed", tool="skill_view", preview="ok")
            elif name == "listskills":
                names = self._list_skills(run)
                out = "skills: " + ",".join(names)
                run.emit("message.delta", delta=out)
                parts.append(out)
            elif name == "tool":
                tname, _, raw = (arg or "").partition(" ")
                try:
                    args = json.loads(raw) if raw.strip() else {}
                except json.JSONDecodeError:
                    args = {}
                run.emit("tool.started", tool=tname, preview=raw[:200])
                result = await self._call_broker(run, tname, args)
                try:
                    parsed = json.loads(result)
                except json.JSONDecodeError:
                    parsed = {}
                err = parsed.get("ok") is False or "error" in parsed
                run.emit("tool.completed", tool=tname, preview=result[:4000], error=err)
                run.last_tool_result = result
            elif name == "proposal":
                pname, _, content = (arg or "").partition("|")
                d = self.profiles_root / run.profile / "pending" / "skills"
                d.mkdir(parents=True, exist_ok=True)
                (d / f"{uuid.uuid4().hex[:8]}.json").write_text(json.dumps({
                    "id": uuid.uuid4().hex, "kind": "skill",
                    "payload": {"action": "create", "name": pname,
                                "content": content.replace("\\n", "\n")}}), encoding="utf-8")
            elif name == "batchproposal":
                # formato do Hermes atual: action=batch + operations[]
                pname, _, content = (arg or "").partition("|")
                d = self.profiles_root / run.profile / "pending" / "skills"
                d.mkdir(parents=True, exist_ok=True)
                (d / f"{uuid.uuid4().hex[:8]}.json").write_text(json.dumps({
                    "id": uuid.uuid4().hex, "subsystem": "skills", "action": "batch",
                    "summary": "create skill",
                    "payload": {"action": "batch", "operations": [
                        {"action": "create", "name": pname, "content": content.replace("\\n", "\n")}]}}),
                    encoding="utf-8")
            elif name == "memproposal":
                d = self.profiles_root / run.profile / "pending" / "memory"
                d.mkdir(parents=True, exist_ok=True)
                (d / f"{uuid.uuid4().hex[:8]}.json").write_text(json.dumps({
                    "id": uuid.uuid4().hex, "kind": "memory",
                    "payload": {"content": arg}}), encoding="utf-8")
            elif name == "approvalreq":
                run.approval = asyncio.get_running_loop().create_future()
                run.emit("approval.request", request_id="req1", command="rm -rf /")
                try:
                    await asyncio.wait_for(run.approval, 10)
                except TimeoutError:
                    pass
            elif name == "interrupted":
                run.emit("run.interrupted")
                return
            elif name == "partial":
                run.emit("run.completed", output="parcial", completed=False, partial=True,
                         usage=self._usage(sess, text, in_tok, out_tok, "parcial"))
                return
            elif name == "fail":
                run.emit("run.failed", error="falha simulada")
                return
            elif name == "fail429":
                key = run.profile
                seen = self.rate_limited.get(key, 0)
                self.rate_limited[key] = seen + 1
                if seen < int(arg or 1):
                    run.emit("run.failed", error="HTTP 429: litellm.RateLimitError: rate limit exceeded")
                    return
            elif name == "empty":
                run.emit("run.completed", output="",
                         usage=self._usage(sess, text, in_tok, out_tok, ""))
                return
            elif name == "echo":  # devolve o ultimo resultado de tool como texto final
                last = run.last_tool_result
                run.emit("message.delta", delta=last[:3000])
                parts.append(last[:3000])
        if run.stop.is_set():
            run.emit("run.cancelled")
            return
        output = "".join(parts)
        sess.messages.append({"role": "assistant", "content": output})
        run.emit("run.completed", output=output,
                 usage=self._usage(sess, text, in_tok, out_tok, output),
                 runtime={"provider": "fake", "model": "fake-1"})
