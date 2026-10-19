#!/usr/bin/env bash
# Docker no ambiente. Uso:
#   docker_exemplo.sh verificar
#   docker_exemplo.sh instalar
#   docker_exemplo.sh rodar NOME IMAGEM PORTA_HOST:PORTA_CONT [args...]
set -euo pipefail
ACAO="${1:?verificar | instalar | rodar}"; shift || true

case "$ACAO" in
  verificar)
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
      echo "disponivel: $(docker --version)"
    elif command -v docker >/dev/null 2>&1; then
      echo "indisponivel: o cliente existe mas o servico nao responde (sem permissao ou sem daemon)"; exit 1
    else
      echo "indisponivel: docker nao instalado"; exit 1
    fi
    ;;
  instalar)
    apt-get update -qq
    apt-get install -y -qq docker.io
    (dockerd >/workspace/logs/dockerd.log 2>&1 &) ; sleep 5
    docker info >/dev/null 2>&1 && echo "docker ok" || { echo "daemon nao subiu; veja /workspace/logs/dockerd.log"; exit 1; }
    ;;
  rodar)
    NOME="${1:?nome}"; IMAGEM="${2:?imagem}"; PORTAS="${3:?host:contêiner}"; shift 3 || true
    docker pull "$IMAGEM"
    docker rm -f "$NOME" >/dev/null 2>&1 || true
    rt-service add "$NOME" -- docker run --rm --name "$NOME" -p "$PORTAS" "$@" "$IMAGEM"
    echo "acompanhe: rt-service logs $NOME 50"
    ;;
  *) echo "acao invalida" >&2; exit 2 ;;
esac
