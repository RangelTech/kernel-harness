#!/usr/bin/env bash
# Gate local (e do CI): constroi a MESMA imagem de producao, sobe e roda o smoke. Falha de verdade (sem `|| true`).
set -euo pipefail
cd "$(dirname "$0")/.."
IMG="kernel-harness-verify:local"
docker build -q -f docker/harness.Dockerfile --build-arg GIT_COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo local)" -t "$IMG" .
cid="$(docker run -d --rm -p 18080:8080 "$IMG")"
trap 'docker stop "$cid" >/dev/null 2>&1 || true' EXIT
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:18080/health >/dev/null 2>&1; then break; fi
  sleep 1
  [ "$i" -eq 30 ] && { echo "health nao respondeu"; docker logs "$cid" | tail -20; exit 1; }
done
curl -fsS http://127.0.0.1:18080/health | grep -q '"status":"ok"'
curl -fsS http://127.0.0.1:18080/version | grep -q '"version"'
echo "verify OK"
