#!/usr/bin/env bash
# Operacoes no Luar (Chatwoot). Requer LUAR_URL, LUAR_CTX e ACCOUNT_ID no ambiente.
# Uso: bash luar.sh conversas [status]
#      bash luar.sh mover_card CARD_ID COLUNA_ID
#      bash luar.sh nota_privada CONVERSA_ID "texto"
#      bash luar.sh etiquetar CONVERSA_ID etiqueta1 [etiqueta2 ...]
set -euo pipefail

_api() {  # _api METODO CAMINHO [corpo_json]
  local metodo="$1" caminho="$2" corpo="${3:-}"
  local args=(-sS --fail-with-body -X "$metodo" "${LUAR_URL:?}/api/v1/accounts/${ACCOUNT_ID:?}${caminho}"
              -H "X-Luar-Context: ${LUAR_CTX:?}" -H "Content-Type: application/json")
  [ -n "$corpo" ] && args+=(-d "$corpo")
  curl "${args[@]}"
}

# json_str TEXTO -> string JSON escapada (evita quebrar o corpo com aspas/quebras de linha)
json_str() { python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"; }

conversas() {  # status: open | pending | resolved | all
  _api GET "/conversations?status=${1:-open}&page=1" \
    | python3 -c 'import sys,json; d=json.load(sys.stdin); [print(c["id"], c.get("status"), (c.get("meta",{}).get("sender",{}) or {}).get("name")) for c in d["data"]["payload"]]'
}

mover_card() {  # kanban: PATCH do card para outra coluna
  _api PATCH "/rt_kanban/cards/${1:?card}" "{\"column_id\": ${2:?coluna}}"
}

nota_privada() {  # interna: o cliente nao ve
  _api POST "/conversations/${1:?conversa}/messages" \
    "{\"content\": $(json_str "${2:?texto}"), \"message_type\": \"outgoing\", \"private\": true}"
}

etiquetar() {  # substitui o conjunto de etiquetas da conversa
  local conversa="${1:?conversa}"; shift
  local lista; lista="$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "$@")"
  _api POST "/conversations/${conversa}/labels" "{\"labels\": ${lista}}"
}

listar_cards() { _api GET "/rt_kanban/cards"; }

if [ "${BASH_SOURCE[0]}" = "$0" ] && [ $# -gt 0 ]; then
  "$@"
fi
