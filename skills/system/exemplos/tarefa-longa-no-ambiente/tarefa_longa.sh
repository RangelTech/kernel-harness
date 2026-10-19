#!/usr/bin/env bash
# Inicia uma tarefa em segundo plano com log. Uso: tarefa_longa.sh NOME comando [args...]
set -euo pipefail
NOME="${1:?nome}"; shift
[ $# -gt 0 ] || { echo "informe o comando" >&2; exit 2; }
[[ "$NOME" =~ ^[a-z0-9_-]+$ ]] || { echo "nome: minusculas, numeros, - e _" >&2; exit 2; }
DIR="${TAREFAS_DIR:-/workspace/logs}"
mkdir -p "$DIR"
rm -f "$DIR/$NOME.fim"
# o bash -c grava o codigo de saida em NOME.fim ao terminar; setsid solta do terminal
setsid nohup bash -c '"$@"; echo $? > "$0"' "$DIR/$NOME.fim" "$@" > "$DIR/$NOME.log" 2>&1 < /dev/null &
echo $! > "$DIR/$NOME.pid"
echo "iniciado: $NOME (log em $DIR/$NOME.log)"
