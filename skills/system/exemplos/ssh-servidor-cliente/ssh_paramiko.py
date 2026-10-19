#!/usr/bin/env python3
"""SSH com paramiko, chave so em memoria. Uso: ssh_paramiko.py USUARIO HOST "comando"."""
import io
import os
import sys


def executar(usuario: str, host: str, comando: str, porta: int = 22) -> tuple[int, str, str]:
    import paramiko  # pip install --user paramiko

    chave_txt = os.environ["SSH_KEY"]
    chave = None
    for cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            chave = cls.from_private_key(io.StringIO(chave_txt))
            break
        except paramiko.SSHException:
            continue
    if chave is None:
        raise SystemExit("formato de chave nao reconhecido")
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # equivale a accept-new
    try:
        cli.connect(host, port=porta, username=usuario, pkey=chave, timeout=15)
        _, out, err = cli.exec_command(comando, timeout=120)
        code = out.channel.recv_exit_status()
        return code, out.read().decode(), err.read().decode()
    finally:
        cli.close()


if __name__ == "__main__":
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    code, so, se = executar(sys.argv[1], sys.argv[2], sys.argv[3])
    print(so, end="")
    print(se, end="", file=sys.stderr)
    sys.exit(code)
