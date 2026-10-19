#!/usr/bin/env bash
# Mostra o estado de uma tarefa iniciada por tarefa_longa.sh. Uso: acompanhar.sh NOME [linhas]
set -uo pipefail
NOME="${1:?nome}"; LINHAS="${2:-15}"
DIR="${TAREFAS_DIR:-/workspace/logs}"
if [ -f "$DIR/$NOME.fim" ]; then
  CODIGO="$(cat "$DIR/$NOME.fim")"
  if [ "$CODIGO" = "0" ]; then echo "concluida"; else echo "falhou (codigo $CODIGO)"; fi
elif [ -f "$DIR/$NOME.pid" ] && kill -0 "$(cat "$DIR/$NOME.pid")" 2>/dev/null; then
  echo "rodando"
else
  echo "desconhecida (sem processo e sem marcador de fim)"
fi
echo "--- ultimas linhas do log ---"
tail -n "$LINHAS" "$DIR/$NOME.log" 2>/dev/null || echo "(sem log)"
