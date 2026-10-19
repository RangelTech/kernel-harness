#!/usr/bin/env bash
# Copia arquivo. Uso: scp_enviar.sh ORIGEM DESTINO  (um dos dois pode ser usuario@host:caminho)
set -euo pipefail
ORIGEM="${1:?origem}"; DESTINO="${2:?destino}"
CHAVE="$(mktemp)"; trap 'rm -f "$CHAVE"' EXIT; chmod 600 "$CHAVE"
printf '%s\n' "${SSH_KEY:?segredo SSH_KEY ausente}" > "$CHAVE"
scp -i "$CHAVE" -o StrictHostKeyChecking=accept-new -o BatchMode=yes "$ORIGEM" "$DESTINO"
