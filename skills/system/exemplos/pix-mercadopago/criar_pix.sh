#!/usr/bin/env bash
# Cria cobranca Pix com curl. Uso: criar_pix.sh VALOR "descricao" REFERENCIA
set -euo pipefail
VALOR="${1:?valor, ex.: 9.90}"; DESC="${2:?descricao}"; REF="${3:?referencia}"
TOKEN_MP="${MP_ACCESS_TOKEN:-${MERCADO_PAGO_TOKEN:?segredo ausente}}"
TETO="${PIX_TETO:-500.00}"
# teto de valor (awk compara como numero)
awk -v v="$VALOR" -v t="$TETO" 'BEGIN{exit !(v>0 && v<=t)}' || { echo "valor fora do teto ($TETO)" >&2; exit 1; }
# chave de idempotencia estavel por referencia
CHAVE="$(printf 'pix:%s' "$REF" | sha256sum | cut -c1-32)"
curl -sS --fail-with-body https://api.mercadopago.com/v1/payments \
  -H "Authorization: Bearer ${TOKEN_MP}" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: ${CHAVE}" \
  -d "{\"transaction_amount\": ${VALOR}, \"description\": \"${DESC}\", \"payment_method_id\": \"pix\", \"external_reference\": \"${REF}\", \"payer\": {\"email\": \"cliente@example.com\"}}" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["id"], d["status"]); print(d["point_of_interaction"]["transaction_data"]["qr_code"])'
# consultar: curl -sS https://api.mercadopago.com/v1/payments/ID -H "Authorization: Bearer ${TOKEN_MP}"
