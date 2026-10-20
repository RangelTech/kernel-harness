"""Zigoto do worker: processo quente, SEM estado de perfil, que gera um filho por run.

Importa o Hermes (e os modulos que ele so carregaria na 1a run) UMA vez, com um HERMES_HOME
descartavel. Depois fica bloqueado em `accept()` num socket unix: custo de CPU ocioso = zero (sem
asyncio, sem threads, sem pollers). Cada pedido do front vira `fork()`: o filho ganha o contexto do
perfil, serve UMA run e morre. Nada de perfil vive no zigoto.

Protocolo (uma linha JSON por conexao): {"profile": str, "port": int, "deadline_s": float} ->
{"pid": int} | {"error": str}.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import socket
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("rt_worker.zygote")

HERE = Path(__file__).parent
SCRATCH_ROOT = Path(os.environ.get("RT_WORKER_SCRATCH", "/tmp/rt-run"))
PROFILES_ROOT = Path(os.environ.get("PROFILES_ROOT", "/opt/data/profiles"))
HERMES_DIR = os.environ.get("RT_HERMES_DIR", "/opt/hermes")
DEFAULT_SOCK = os.environ.get("RT_WORKER_SOCK", "/tmp/rt-zygote.sock")

# Config do HERMES_HOME descartavel de cada filho. Com `multiplex_profiles` o Hermes serve o perfil
# pedido em /p/<perfil>/ com config/credenciais DELE (contextvar), sem tocar no os.environ.
SCRATCH_CONFIG = {
    "model": {"default": "x", "provider": "custom", "base_url": "http://127.0.0.1:1/v1",
              "api_key": "x"},
    "platform_toolsets": {"api_server": ["skills"]},
    "gateway": {"multiplex_profiles": True, "strict": True,
                "api_server": {"max_concurrent_runs": 2}},
}


BASE_MODULES: frozenset = frozenset()


def warm_module_names() -> list[str]:
    p = HERE / "warm_modules.txt"
    if not p.exists():
        return []
    return [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def install_yaml_memo(maxsize: int = 4096) -> None:
    """Memoiza `hermes_yaml.safe_load` por conteudo do documento.

    O Hermes le ~55 manifestos de plugin (YAML puro-Python, ~60 ms cada) a CADA run porque o gerenciador
    de plugins e por HOME de perfil. Como o conteudo e igual entre perfis, memoizar corta ~4 s de CPU por
    run. Devolve copia profunda (o chamador pode mutar). Populado no zigoto, herdado pelos filhos."""
    import copy

    import hermes_yaml

    orig = hermes_yaml.safe_load
    memo: dict = {}

    def safe_load(stream):
        doc = stream if isinstance(stream, (str, bytes)) else stream.read()
        hit = memo.get(doc)
        if hit is None:
            hit = (orig(doc),)
            if len(memo) < maxsize:
                memo[doc] = hit
        return copy.deepcopy(hit[0])

    hermes_yaml.safe_load = safe_load
    hermes_yaml._rt_memo = memo


def prime_yaml_memo() -> int:
    """Le uma vez todos os manifestos de plugin da instalacao (enche o memo antes do fork)."""
    import hermes_yaml
    n = 0
    for f in Path(HERMES_DIR).glob("locales/*.yaml"):
        try:
            with f.open("r", encoding="utf-8-sig") as h:
                hermes_yaml.safe_load(h.read())
            n += 1
        except Exception:  # noqa: BLE001
            pass
    for f in Path(HERMES_DIR).glob("plugins/**/plugin.y*ml"):
        try:
            hermes_yaml.safe_load(f.read_text(encoding="utf-8"))
            n += 1
        except Exception:  # noqa: BLE001
            pass
    return n


def preload(names: list[str]) -> tuple[int, int]:
    """Importa os modulos da lista; falha de um nao derruba o zigoto. -> (ok, falhas)."""
    import importlib
    ok = bad = 0
    for n in names:
        try:
            importlib.import_module(n)
            ok += 1
        except BaseException:  # noqa: BLE001 - import com efeito colateral/SystemExit
            bad += 1
    return ok, bad


def make_scratch_home(tag: str, profile: str, api_key: str) -> Path:
    """HERMES_HOME descartavel do filho: `profiles/<perfil>` aponta para a pasta real do perfil."""
    home = SCRATCH_ROOT / tag
    (home / "profiles").mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(json.dumps(SCRATCH_CONFIG), encoding="utf-8")
    (home / ".no-bundled-skills").write_text("", encoding="utf-8")
    env = home / ".env"
    fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f"API_SERVER_KEY={api_key}\n")
    link = home / "profiles" / profile
    if link.is_symlink() or link.exists():
        link.unlink()
    os.symlink(PROFILES_ROOT / profile, link)
    return home


def child_main(spec: dict) -> None:
    """Corpo do filho (apos o fork). Nunca retorna."""
    code = 0
    home: Path | None = None
    try:
        os.setsid()  # grupo proprio: o front mata o grupo e leva junto qualquer subprocesso de ferramenta
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)  # o zigoto ignora; o Hermes precisa do wait()
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        import secrets
        tag = f"{os.getpid()}-{secrets.token_hex(4)}"
        home = make_scratch_home(tag, spec["profile"], secrets.token_hex(24))
        os.environ["HERMES_HOME"] = str(home)
        for k, v in (line.rstrip("\n").split("=", 1) for line in open(home / ".env")):
            os.environ[k] = v
        import asyncio
        if os.environ.get("RT_WORKER_PROFILE"):
            import cProfile
            prof = cProfile.Profile()
            try:
                prof.runcall(asyncio.run, _serve(spec, home))
            finally:
                prof.dump_stats(f"{os.environ['RT_WORKER_PROFILE']}/child-{os.getpid()}.prof")
        else:
            asyncio.run(_serve(spec, home))
    except SystemExit as exc:
        code = int(exc.code or 0) if isinstance(exc.code, int) else 1
    except BaseException:  # noqa: BLE001
        log.exception("filho do zigoto falhou")
        code = 1
    finally:
        if home is not None:
            shutil.rmtree(home, ignore_errors=True)
        dump = os.environ.get("RT_WORKER_LOG_NEW_MODULES")
        if dump:
            try:
                new = sorted(set(sys.modules) - BASE_MODULES)
                Path(dump, f"newmods-{os.getpid()}.txt").write_text(chr(10).join(new), encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
        os._exit(code)


async def _serve(spec: dict, home: Path) -> None:
    import asyncio
    from types import SimpleNamespace

    from agent.secret_scope import set_multiplex_active
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stop.set)
    loop.add_signal_handler(signal.SIGINT, stop.set)
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={
        "host": "127.0.0.1", "port": int(spec["port"]), "key": os.environ["API_SERVER_KEY"]}))
    set_multiplex_active(True)
    adapter.gateway_runner = SimpleNamespace(config=SimpleNamespace(multiplex_profiles=True))
    if not await adapter.connect():
        raise RuntimeError("API server do filho nao subiu")
    loop.call_later(float(spec.get("deadline_s", 3600)), stop.set)
    await stop.wait()
    try:
        await asyncio.wait_for(adapter.disconnect(), 5)
    except Exception:  # noqa: BLE001
        pass


async def _fake_llm(port: int):
    """Servidor OpenAI-compativel minimo (so para o aquecimento: responde "ok")."""
    from aiohttp import web

    async def chat(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        if not body.get("stream"):
            return web.json_response({"id": "w", "object": "chat.completion", "created": 0,
                "model": "warm", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        base = {"id": "w", "object": "chat.completion.chunk", "created": 0, "model": "warm"}
        for delta, fin in (({"role": "assistant", "content": "ok"}, None), ({}, "stop")):
            ch = {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": fin}]}
            await resp.write(b"data: " + json.dumps(ch).encode() + b"\n\n")
        usage = {**base, "choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                                                   "total_tokens": 2}}
        await resp.write(b"data: " + json.dumps(usage).encode() + b"\n\ndata: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner


def _template_config() -> dict:
    """config.yaml de um perfil real (so a forma: skills/plugins/toolsets), com o modelo trocado."""

    import hermes_yaml
    for d in sorted(PROFILES_ROOT.glob("u-*")):
        f = d / "config.yaml"
        if f.is_file():
            cfg = hermes_yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            cfg["model"] = {"default": "warm", "provider": "custom",
                            "base_url": "http://127.0.0.1:18999/v1", "api_key": "x"}
            for k in ("auxiliary", "compression"):
                cfg.pop(k, None)
            return cfg
    return {"model": {"default": "warm", "provider": "custom",
                      "base_url": "http://127.0.0.1:18999/v1", "api_key": "x"}}


async def _warm_run(boot: Path) -> None:
    """Executa UMA run completa (modelo falso) no proprio zigoto para popular os caches preguicosos
    do Hermes (descoberta de plugins, registro de ferramentas, prompt builder). Os filhos herdam."""
    import asyncio
    from types import SimpleNamespace

    import aiohttp
    from agent.secret_scope import set_multiplex_active
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter

    prof = boot / "profiles" / "u-warmup"
    for sub in ("plugins", "skills", "memories", "sessions"):
        (prof / sub).mkdir(parents=True, exist_ok=True)
    (prof / "config.yaml").write_text(json.dumps(_template_config()), encoding="utf-8")
    key = "warmup-key-0123456789abcdef"
    (prof / ".env").write_text(f"API_SERVER_KEY={key}\nOPENAI_API_KEY=x\n", encoding="utf-8")
    plug = Path("/opt/rt/plugins/rt-broker")
    if plug.exists() and not (prof / "plugins" / "rt-broker").exists():
        os.symlink(plug, prof / "plugins" / "rt-broker")
    (boot / ".env").write_text(f"API_SERVER_KEY={key}\n", encoding="utf-8")
    os.environ["API_SERVER_KEY"] = key
    llm = await _fake_llm(18999)
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={
        "host": "127.0.0.1", "port": 18998, "key": key}))
    set_multiplex_active(True)
    adapter.gateway_runner = SimpleNamespace(config=SimpleNamespace(multiplex_profiles=True))
    try:
        if not await adapter.connect():
            return
        h = {"Authorization": f"Bearer {key}"}
        async with aiohttp.ClientSession() as s:
            for _ in range(2):
                r = await s.post("http://127.0.0.1:18998/p/u-warmup/v1/runs", headers=h,
                                 json={"input": "oi", "session_id": "warm", "instructions": "x"})
                rid = (await r.json())["run_id"]
                async with s.get(f"http://127.0.0.1:18998/p/u-warmup/v1/runs/{rid}/events",
                                 headers=h, timeout=aiohttp.ClientTimeout(total=90)) as ev:
                    async for raw in ev.content:
                        if b"run.completed" in raw or b"run.failed" in raw:
                            break
    finally:
        await adapter.disconnect()
        await llm.cleanup()
        loop = asyncio.get_running_loop()
        await loop.shutdown_default_executor()


def warm_up(boot: Path) -> None:
    import asyncio
    t = time.time()
    try:
        asyncio.run(asyncio.wait_for(_warm_run(boot), 120))
        log.info("aquecimento de run completa em %.1fs", time.time() - t)
    except BaseException:  # noqa: BLE001
        log.exception("aquecimento falhou (segue sem ele)")


def serve_forever(sock_path: str = DEFAULT_SOCK) -> None:
    t0 = time.time()
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    boot = SCRATCH_ROOT / "zygote-home"
    boot.mkdir(parents=True, exist_ok=True)
    (boot / "config.yaml").write_text(json.dumps(SCRATCH_CONFIG), encoding="utf-8")
    (boot / ".no-bundled-skills").write_text("", encoding="utf-8")
    os.environ["HERMES_HOME"] = str(boot)
    if HERMES_DIR not in sys.path:
        sys.path.insert(0, HERMES_DIR)
    install_yaml_memo()
    import gateway.platforms.api_server  # noqa: F401
    ok, bad = preload(warm_module_names())
    log.info("manifestos de plugin no memo: %d", prime_yaml_memo())
    if os.environ.get("RT_WORKER_WARM_RUN", "0") == "1":
        warm_up(boot)
    # fork() so e seguro sem threads vivas: aviso alto se alguma biblioteca criou uma.
    extra = [t.name for t in threading.enumerate() if t is not threading.main_thread()]
    log.info("zigoto quente em %.1fs (modulos ok=%d falha=%d threads_extras=%s)",
             time.time() - t0, ok, bad, extra)
    global BASE_MODULES
    BASE_MODULES = frozenset(sys.modules)
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)  # filhos se reaparam sozinhos
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    os.chmod(sock_path, 0o600)
    srv.listen(64)
    while True:
        try:
            conn, _ = srv.accept()
        except InterruptedError:
            continue
        try:
            raw = b""
            while not raw.endswith(b"\n") and len(raw) < 65536:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                raw += chunk
            spec = json.loads(raw.decode())
            if not isinstance(spec.get("profile"), str) or "/" in spec["profile"]:
                raise ValueError("perfil invalido")
            pid = os.fork()
            if pid == 0:
                srv.close()
                conn.close()
                child_main(spec)
            conn.sendall(json.dumps({"pid": pid}).encode() + b"\n")
        except Exception as exc:  # noqa: BLE001
            try:
                conn.sendall(json.dumps({"error": type(exc).__name__}).encode() + b"\n")
            except OSError:
                pass
        finally:
            conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    serve_forever(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SOCK)
