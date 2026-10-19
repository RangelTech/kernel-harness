#!/usr/bin/env bash
# Executa um comando no servidor do cliente. Uso: ssh_exec.sh USUARIO@HOST "comando" [porta]
set -euo pipefail
ALVO="${1:?usuario@host}"; CMD="${2:?comando}"; PORTA="${3:-22}"
CHAVE="$(mktemp)"                      # arquivo efemero, so o dono le
trap 'rm -f "$CHAVE"' EXIT             # apaga a chave ao sair, com erro ou nao
chmod 600 "$CHAVE"
printf '%s\n' "${SSH_KEY:?segredo SSH_KEY ausente}" > "$CHAVE"
ssh -i "$CHAVE" -p "$PORTA" -o StrictHostKeyChecking=accept-new -o BatchMode=yes \
    -o ConnectTimeout=15 "$ALVO" "$CMD"
