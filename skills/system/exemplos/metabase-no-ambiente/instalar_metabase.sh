#!/usr/bin/env bash
# Instala o Metabase como servico. Uso: instalar_metabase.sh [jar|docker]
set -euo pipefail
MODO="${1:-}"
if [ -z "$MODO" ]; then
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then MODO=docker; else MODO=jar; fi
fi
mkdir -p /workspace/logs /workspace/metabase-data
echo "modo: $MODO"

case "$MODO" in
  jar)
    mkdir -p /opt/metabase
    apt-get update -qq
    apt-get install -y -qq default-jre-headless curl
    curl -fsSL -o /opt/metabase/metabase.jar https://downloads.metabase.com/latest/metabase.jar
    [ "$(stat -c %s /opt/metabase/metabase.jar)" -gt 50000000 ] || { echo "download incompleto" >&2; exit 1; }
    chown -R ws:ws /workspace/metabase-data 2>/dev/null || true
    rt-service add metabase --cwd /opt/metabase \
      --env MB_DB_FILE=/workspace/metabase-data/metabase.db \
      --env MB_JETTY_PORT=3000 --env MB_JETTY_HOST=0.0.0.0 \
      -- java -Xmx1g -jar /opt/metabase/metabase.jar
    ;;
  docker)
    docker pull metabase/metabase:latest
    docker rm -f metabase >/dev/null 2>&1 || true
    # primeiro plano (sem -d): o rt-service supervisiona e reinicia
    rt-service add metabase -- docker run --rm --name metabase -p 3000:3000 \
      -e MB_DB_FILE=/metabase-data/metabase.db -v /workspace/metabase-data:/metabase-data \
      metabase/metabase:latest
    ;;
  *) echo "modo invalido: use jar ou docker" >&2; exit 2 ;;
esac
echo "acompanhe: rt-service logs metabase 50 ; curl -s localhost:3000/api/health"
echo INSTALADO
