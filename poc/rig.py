"""Rig local de POC (Docker Desktop): fake LLM + broker de mentira + worker rt-hermes + perfis.

Uso (a partir da raiz do repo):  python poc/rig.py up | down | gen <...> | logs
Tudo em containers com prefixo rt-poc-, rede poc-net, volumes rt-poc-*.
Nunca toca producao; chaves e segredos sao sinteticos.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ["MSYS_NO_PATHCONV"] = "1"
REPO = Path(__file__).resolve().parent.parent
RUN = REPO / "poc" / ".run"
IMG = os.environ.get("RT_HERMES_IMAGE", "rt-hermes:dev")
NET = "rt-poc-net"
WORKER = "rt-poc-worker"
PORT = int(os.environ.get("RT_POC_PORT", "28642"))
DEFAULT_KEY = "default-profile-key-0123456789abcdef"
MASTER = "poc-master-secret-0123456789abcdef"
BROKER_TOKEN = "pooltoken-0123456789"
VOL_DATA, VOL_SYS, VOL_TENANT, VOL_USERS = "rt-poc-data", "rt-poc-sys", "rt-poc-tenant", "rt-poc-users"


def host(p: Path) -> str:
    return str(p).replace("\\", "/")


def sh(*args, check=True, input=None, timeout=300):
    r = subprocess.run(list(args), capture_output=True, text=True, encoding="utf-8", errors="replace", input=input, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:6])}...: rc={r.returncode}\n{r.stdout}\n{r.stderr}")
    return r


def docker(*args, **kw):
    return sh("docker", *args, **kw)


def exists(name):
    return docker("inspect", name, check=False).returncode == 0


def ensure_net():
    if docker("network", "inspect", NET, check=False).returncode != 0:
        docker("network", "create", NET)


def rm(*names):
    for n in names:
        docker("rm", "-f", n, check=False)


def root_sh(script: str, *mounts: str, image=None):
    """Roda um script sh como root numa imagem rt-hermes (entrypoint sh) com volumes/mounts."""
    args = ["run", "--rm", "--entrypoint", "sh", "-v", f"{host(REPO)}:/repo:ro"]
    for m in mounts:
        args += ["-v", m]
    args += [image or IMG, "-c", script]
    return docker(*args)


def up_support():
    ensure_net()
    RUN.mkdir(parents=True, exist_ok=True)
    (RUN / "logs").mkdir(exist_ok=True)
    rm("rt-poc-fakellm", "rt-poc-broker")
    docker("run", "-d", "--name", "rt-poc-fakellm", "--network", NET, "--network-alias", "fakellm", "-p", "29000:9000",
           "-v", f"{host(REPO / 'poc')}:/app:ro", "-v", f"{host(RUN / 'logs')}:/logs", "python:3.12-slim",
           "python", "-u", "/app/fake_llm.py")
    docker("run", "-d", "--name", "rt-poc-broker", "--network", NET, "--network-alias", "broker", "-p", "29099:8099",
           "-e", f"RT_BROKER_TOKEN={BROKER_TOKEN}", "-v", f"{host(REPO / 'poc')}:/app:ro",
           "-v", f"{host(RUN / 'logs')}:/logs", "python:3.12-slim", "python", "-u", "/app/broker_stub.py")


def reset_logs():
    for f in (RUN / "logs").glob("*.jsonl"):
        f.unlink()


def worker_args(extra_env: dict | None = None, extra_mounts: list[str] | None = None):
    env = {"API_SERVER_KEY": DEFAULT_KEY, "RT_BROKER_URL": "http://broker:8099/tool-call",
           "RT_BROKER_TOKEN": BROKER_TOKEN, "OPENAI_API_KEY": "fake-provider-key-0000000000"}
    env.update(extra_env or {})
    a = ["run", "-d", "--name", WORKER, "--network", NET, "-p", f"{PORT}:8642",
         "-v", f"{VOL_DATA}:/opt/data", "-v", f"{VOL_SYS}:/skills/system:ro",
         "-v", f"{VOL_TENANT}:/skills/tenant:ro", "-v", f"{VOL_USERS}:/srv/hermes/users"]
    for m in extra_mounts or []:
        a += ["-v", m]
    for k, v in env.items():
        a += ["-e", f"{k}={v}"]
    return a + [IMG]


def start_worker(extra_env=None, extra_mounts=None, wait_health=True):
    rm(WORKER)
    docker(*worker_args(extra_env, extra_mounts))
    if wait_health:
        wait_up()


def wait_up(timeout=90):
    import urllib.request
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(1.5)
    raise RuntimeError("worker nao ficou saudavel; logs:\n" + docker("logs", "--tail", "60", WORKER, check=False).stdout)


def fresh_volumes():
    rm(WORKER)
    for v in (VOL_DATA, VOL_SYS, VOL_TENANT, VOL_USERS):
        docker("volume", "rm", "-f", v, check=False)
        docker("volume", "create", v)
    # permissao: tudo legivel pelo uid 10000; /srv/hermes/users gravavel
    root_sh("chown 10000:10000 /d /u /t /s; chmod 755 /t /s",
            f"{VOL_DATA}:/d", f"{VOL_USERS}:/u", f"{VOL_TENANT}:/t", f"{VOL_SYS}:/s")


def gen_profile(tenant, template, user, manifest, **kw):
    """Roda o gerador DENTRO de um container (python do venv do Hermes) gravando em /opt/data/profiles."""
    os.environ["RT_PROFILE_MASTER_SECRET"] = MASTER
    mpath = RUN / f"manifest-{tenant}-{template}-{user}.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    llm_base_url = kw.get("llm_base_url", "http://fakellm:9000/v1")
    key_cmd = kw.get("llm_key_cmd", "")
    ver = kw.get("template_version", "v1")
    cmd = ["python", "/repo/profiles/profile_config.py", "--profiles-root", "/opt/data/profiles",
           "--tenant", tenant, "--template", template, "--manifest", f"/m/{mpath.name}",
           "--users-root", "/srv/hermes/users", "--llm-base-url", llm_base_url, "--template-version", ver,
           "--uid", "10000", "--print-key"]
    if user:
        cmd += ["--user", user]
    if key_cmd:
        cmd += ["--llm-key-cmd", key_cmd]
    args = ["run", "--rm", "--entrypoint", "python", "-e", f"RT_PROFILE_MASTER_SECRET={MASTER}",
            "-v", f"{host(REPO)}:/repo:ro", "-v", f"{VOL_DATA}:/opt/data", "-v", f"{VOL_USERS}:/srv/hermes/users",
            "-v", f"{host(RUN)}:/m:ro", "rt-poc-gen:latest"] + cmd[1:]
    r = docker(*args)
    out = json.loads(r.stdout.strip().splitlines()[-1])
    # garante dono do diretorio de usuarios
    root_sh("chown -R 10000:10000 /srv/hermes/users /opt/data/profiles", f"{VOL_USERS}:/srv/hermes/users", f"{VOL_DATA}:/opt/data")
    return out


def put_files(volume, mount, files: dict[str, str]):
    """Escreve arquivos de texto num volume (como root, depois chown 10000)."""
    script = ""
    for rel, content in files.items():
        p = f"{mount}/{rel}"
        script += f"mkdir -p \"$(dirname '{p}')\" && cat > '{p}' <<'RTEOF'\n{content}\nRTEOF\n"
    script += f"chown -R 10000:10000 {mount}; chmod -R a+rX {mount}"
    return root_sh(script, f"{volume}:{mount}")


def exec_in(script, user="hermes"):
    return docker("exec", "-u", user, WORKER, "sh", "-c", script, check=False)


def req_log():
    """So as chamadas POST ao LLM (ignora GET /models)."""
    p = RUN / "logs" / "requests.jsonl"
    return [r for r in (json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()) if r.get("method") == "POST"] if p.exists() else []


def broker_log():
    p = RUN / "logs" / "broker.jsonl"
    return [r for r in (json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()) if r.get("method") == "POST"] if p.exists() else []


def down():
    rm(WORKER, "rt-poc-fakellm", "rt-poc-broker")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "up"
    if cmd == "up":
        up_support()
        fresh_volumes()
        print("support up; volumes fresh")
    elif cmd == "down":
        down()
    elif cmd == "logs":
        print(docker("logs", "--tail", sys.argv[2] if len(sys.argv) > 2 else "80", WORKER, check=False).stdout)


def bootstrap(extra_env=None, extra_mounts=None, skills=True):
    """Ambiente limpo: suporte (fake LLM + broker) + volumes novos + skills de exemplo + worker de pe."""
    if not (exists("rt-poc-fakellm") and exists("rt-poc-broker")):
        up_support()
    fresh_volumes()
    reset_logs()
    if skills:
        put_files(VOL_SYS, "/skills/system", {"sys-ola/SKILL.md": "---\nname: sys-ola\ndescription: Skill de sistema de teste\n---\n# sys\nCorpo sistema"})
        put_files(VOL_TENANT, "/skills/tenant", {"tpl1/v1/emp-ola/SKILL.md": "---\nname: emp-ola\ndescription: Skill da empresa\n---\n# emp\nCorpo empresa"})
        root_sh("cd /t/tpl1 && ln -sfn v1 current && chown -h 10000:10000 current", f"{VOL_TENANT}:/t")
    start_worker(extra_env, extra_mounts)


def save(name, data):
    (REPO / "poc" / "results").mkdir(exist_ok=True)
    (REPO / "poc" / "results" / f"{name}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return data
