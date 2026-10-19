#!/usr/bin/env python3
"""Lista os ultimos e-mails da caixa de entrada. Uso: ler_email.py [N]."""
import email
import imaplib
import os
import sys
from email.header import decode_header, make_header


def texto(valor: str | None) -> str:
    return str(make_header(decode_header(valor or "")))


def ultimos(n: int = 10) -> list[dict]:
    out = []
    with imaplib.IMAP4_SSL(os.environ["IMAP_HOST"], 993) as imap:
        imap.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        imap.select("INBOX", readonly=True)  # readonly: nao marca como lido
        _, dados = imap.search(None, "ALL")
        for num in dados[0].split()[-n:][::-1]:
            _, parte = imap.fetch(num, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            h = email.message_from_bytes(parte[0][1])
            out.append({"de": texto(h["From"]), "assunto": texto(h["Subject"]), "data": h["Date"]})
    return out


if __name__ == "__main__":
    for m in ultimos(int(sys.argv[1]) if len(sys.argv) > 1 else 10):
        print(f'{m["data"]} | {m["de"]} | {m["assunto"]}')
