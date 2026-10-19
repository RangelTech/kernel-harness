#!/usr/bin/env python3
"""Cria uma cobranca Pix no Mercado Pago. Uso: criar_pix.py VALOR "descricao" REFERENCIA [email]."""
import base64
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

API = "https://api.mercadopago.com/v1/payments"


def token() -> str:
    return os.environ.get("MP_ACCESS_TOKEN") or os.environ["MERCADO_PAGO_TOKEN"]


def valor_valido(texto: str) -> Decimal:
    try:
        v = Decimal(texto.replace(",", ".")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise SystemExit(f"valor invalido: {texto!r}") from None
    teto = Decimal(os.environ.get("PIX_TETO", "500.00"))
    if v <= 0:
        raise SystemExit("o valor deve ser maior que zero")
    if v > teto:
        raise SystemExit(f"valor {v} acima do teto {teto}; confirme com o usuario antes de aumentar PIX_TETO")
    return v


def chave_idempotencia(referencia: str) -> str:
    """Mesma referencia -> mesma chave -> a API nao cria cobranca duplicada."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "pix:" + referencia))


def criar(valor: Decimal, descricao: str, referencia: str, email: str) -> dict:
    corpo = {
        "transaction_amount": float(valor),  # a API pede numero JSON; o valor ja foi validado em Decimal
        "description": descricao,
        "payment_method_id": "pix",
        "external_reference": referencia,
        "payer": {"email": email},
    }
    req = urllib.request.Request(
        API, data=json.dumps(corpo).encode(), method="POST",
        headers={
            "Authorization": "Bearer " + token(),
            "Content-Type": "application/json",
            "X-Idempotency-Key": chave_idempotencia(referencia),
        })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Mercado Pago recusou ({e.code}): {e.read().decode()[:500]}") from e


def main(argv: list[str]) -> None:
    if len(argv) < 4:
        raise SystemExit(__doc__)
    valor = valor_valido(argv[1])
    email = argv[4] if len(argv) > 4 else "cliente@example.com"
    d = criar(valor, argv[2], argv[3], email)
    dados = d["point_of_interaction"]["transaction_data"]
    out = Path(os.environ.get("OUT_DIR", "/workspace/out"))
    out.mkdir(parents=True, exist_ok=True)
    png = out / f"pix-{argv[3]}.png"
    png.write_bytes(base64.b64decode(dados["qr_code_base64"]))
    print("id:", d["id"], "status:", d["status"])
    print("copia-e-cola:", dados["qr_code"])
    print("qr:", png)


if __name__ == "__main__":
    main(sys.argv)
