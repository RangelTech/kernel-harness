#!/usr/bin/env python3
"""Envia e-mail. Uso: enviar_email.py DESTINO "assunto" "corpo" [caminho_do_anexo]."""
import mimetypes
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path


def montar(origem: str, destino: str, assunto: str, corpo: str, anexo: str | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = origem, destino, assunto
    msg.set_content(corpo)
    if anexo:
        p = Path(anexo)
        tipo, _ = mimetypes.guess_type(p.name)
        principal, sub = (tipo or "application/octet-stream").split("/", 1)
        msg.add_attachment(p.read_bytes(), maintype=principal, subtype=sub, filename=p.name)
    return msg


def enviar(msg: EmailMessage) -> None:
    host, porta = os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "465"))
    ctx = ssl.create_default_context()
    if porta == 465:
        srv = smtplib.SMTP_SSL(host, porta, context=ctx, timeout=30)
    else:
        srv = smtplib.SMTP(host, porta, timeout=30)
        srv.starttls(context=ctx)
    with srv:
        srv.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        srv.send_message(msg)


if __name__ == "__main__":
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    enviar(montar(os.environ["SMTP_USER"], sys.argv[1], sys.argv[2], sys.argv[3],
                  sys.argv[4] if len(sys.argv) > 4 else None))
    print("enviado para", sys.argv[1])
