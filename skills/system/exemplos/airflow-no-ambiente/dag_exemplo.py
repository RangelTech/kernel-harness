"""DAG de exemplo: roda todo dia as 8h (Brasilia). Copie para /workspace/airflow/dags/."""
import pendulum
from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator


def gerar_relatorio():
    print("relatorio gerado")  # troque pela logica real; segredos entram por Connections/variaveis do Airflow


with DAG(
    "exemplo_diario",
    start_date=pendulum.datetime(2026, 1, 1, tz="America/Sao_Paulo"),
    schedule="0 8 * * *",
    catchup=False,
    default_args={"retries": 2, "retry_delay": pendulum.duration(minutes=5)},
) as dag:
    preparar = BashOperator(task_id="preparar", bash_command="mkdir -p /workspace/out")
    relatorio = PythonOperator(task_id="relatorio", python_callable=gerar_relatorio)
    preparar >> relatorio
