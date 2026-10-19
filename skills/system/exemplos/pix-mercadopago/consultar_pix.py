#!/usr/bin/env python3
"""Consulta o status de um pagamento. Uso: consultar_pix.py ID_DO_PAGAMENTO."""
import json
import os
import sys
import urllib.request


def status(pagamento_id: str) -> dict:
    tk = os.environ.get("MP_ACCESS_TOKEN") or os.environ["MERCADO_PAGO_TOKEN"]
    req = urllib.request.Request(f"https://api.mercadopago.com/v1/payments/{pagamento_id}",
                                 headers={"Authorization": "Bearer " + tk})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    d = status(sys.argv[1])
    # approved = pago; pending = aguardando; rejected / cancelled / expired = nao pago
    print(d["status"], d.get("status_detail"), d.get("transaction_amount"))
