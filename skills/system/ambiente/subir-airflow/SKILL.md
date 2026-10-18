---
name: subir-airflow
description: Instalar o Apache Airflow no ambiente da empresa para orquestrar rotinas e pipelines (DAGs), como serviço supervisionado.
---

# Airflow no ambiente

Uso típico: pipelines que rodam em horários, com histórico e tela de acompanhamento. Para tarefas simples de aviso/e-mail diário, prefira `orquestracao-e-rotinas` (agendamento da plataforma).

1. Estado do ambiente: `rt_env_status`.
2. Ambiente Python isolado:
   `apt-get update && apt-get install -y python3-venv python3-dev build-essential`
   `python3 -m venv /opt/airflow-venv`
   Instale com as constraints oficiais da versão do Python do ambiente (`python3 --version`):
   `/opt/airflow-venv/bin/pip install -q "apache-airflow==2.10.5" --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-2.10.5/constraints-3.11.txt"` (troque `3.11` pela versão do Python).
3. Pastas persistentes: `mkdir -p /workspace/airflow/dags /workspace/airflow/logs`.
4. Configuração por variáveis (no serviço): `AIRFLOW_HOME=/workspace/airflow`, `AIRFLOW__CORE__LOAD_EXAMPLES=False`.
5. Inicializar e criar o administrador (senha forte gerada na hora, mostrada só ao usuário):
   `AIRFLOW_HOME=/workspace/airflow /opt/airflow-venv/bin/airflow db migrate`
   `AIRFLOW_HOME=/workspace/airflow /opt/airflow-venv/bin/airflow users create --username admin --role Admin --firstname Admin --lastname Empresa --email <email do usuario> --password <senha gerada>`
6. Dois serviços:
   `rt-service add airflow-web --env AIRFLOW_HOME=/workspace/airflow -- /opt/airflow-venv/bin/airflow webserver --port 8080`
   `rt-service add airflow-scheduler --env AIRFLOW_HOME=/workspace/airflow -- /opt/airflow-venv/bin/airflow scheduler`
7. Verifique: `rt-service logs airflow-web 50` e `curl -s localhost:8080/health`.
8. DAGs: arquivos `.py` em `/workspace/airflow/dags/`. Exemplo mínimo de DAG diário às 8h (horário de Brasília):

```python
import pendulum
from airflow import DAG
from airflow.operators.bash import BashOperator

with DAG("exemplo_diario", start_date=pendulum.datetime(2026, 1, 1, tz="America/Sao_Paulo"),
         schedule="0 8 * * *", catchup=False) as dag:
    BashOperator(task_id="tarefa", bash_command="echo ok")
```

9. Publicar com `publicar-app` (`app=airflow`, `port=8080`) **somente** depois de criar o administrador com senha forte.

## Cuidados
- Airflow pede ~2 GB de RAM; avise se o plano não comporta.
- Segredos de conexão entram pela tela de Connections do Airflow pelo próprio usuário, não em arquivos de DAG.
