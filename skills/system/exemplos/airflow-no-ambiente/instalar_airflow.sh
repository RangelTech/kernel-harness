#!/usr/bin/env bash
# Instala o Airflow e registra os servicos. Rode em segundo plano com log (demora varios minutos).
# Uso: instalar_airflow.sh [email_do_admin] [versao]
set -euo pipefail
EMAIL="${1:-admin@example.com}"
VERSAO="${2:-2.10.5}"
HOME_AF=/workspace/airflow
VENV=/opt/airflow-venv

apt-get update -qq
apt-get install -y -qq python3-venv python3-dev build-essential
python3 -m venv "$VENV"
PY="$("$VENV/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
"$VENV/bin/pip" install -q "apache-airflow==${VERSAO}" \
  --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${VERSAO}/constraints-${PY}.txt"

mkdir -p "$HOME_AF/dags" "$HOME_AF/logs" /workspace/logs
export AIRFLOW_HOME="$HOME_AF" AIRFLOW__CORE__LOAD_EXAMPLES=False
"$VENV/bin/airflow" db migrate

SENHA="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
( umask 077; printf '%s\n' "$SENHA" > "$HOME_AF/admin_senha.txt" )
"$VENV/bin/airflow" users create --username admin --role Admin --firstname Admin \
  --lastname Empresa --email "$EMAIL" --password "$SENHA"
chown -R ws:ws "$HOME_AF" 2>/dev/null || true

rt-service add airflow-web --env AIRFLOW_HOME="$HOME_AF" --env AIRFLOW__CORE__LOAD_EXAMPLES=False \
  -- "$VENV/bin/airflow" webserver --port 8080
rt-service add airflow-scheduler --env AIRFLOW_HOME="$HOME_AF" --env AIRFLOW__CORE__LOAD_EXAMPLES=False \
  -- "$VENV/bin/airflow" scheduler

echo "senha do admin em $HOME_AF/admin_senha.txt (entregue ao usuario e apague)"
echo INSTALADO
