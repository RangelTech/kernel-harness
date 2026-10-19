#!/usr/bin/env python3
"""Tarefa longa em segundo plano, em Python. Uso: tarefa_longa.py iniciar NOME cmd... | estado NOME."""
import os
import subprocess
import sys
from pathlib import Path


def _dir() -> Path:
    d = Path(os.environ.get("TAREFAS_DIR", "/workspace/logs"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def iniciar(nome: str, comando: list[str]) -> int:
    d = _dir()
    (d / f"{nome}.fim").unlink(missing_ok=True)
    # o bash grava o codigo de saida em NOME.fim quando o comando acaba
    wrapper = f'"$@"; echo $? > "{d / (nome + ".fim")}"'
    with open(d / f"{nome}.log", "wb") as log:
        p = subprocess.Popen(["bash", "-c", wrapper, "_", *comando], stdout=log, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
    (d / f"{nome}.pid").write_text(str(p.pid))
    return p.pid


def estado(nome: str) -> str:
    d = _dir()
    fim = d / f"{nome}.fim"
    if fim.exists():
        codigo = fim.read_text().strip()
        return "concluida" if codigo == "0" else f"falhou (codigo {codigo})"
    pid = d / f"{nome}.pid"
    if pid.exists():
        try:
            os.kill(int(pid.read_text()), 0)
            return "rodando"
        except OSError:
            pass
    return "desconhecida"


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "iniciar":
        print("pid", iniciar(sys.argv[2], sys.argv[3:]))
    elif len(sys.argv) == 3 and sys.argv[1] == "estado":
        print(estado(sys.argv[2]))
    else:
        raise SystemExit(__doc__)
