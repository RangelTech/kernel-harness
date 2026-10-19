---
name: exemplo-airflow-no-ambiente
description: Script de instalação do Apache Airflow no ambiente (venv, banco, serviços rt-service) e uma DAG de exemplo.
---

# Exemplo: Airflow no ambiente

Referência, não obrigação. O passo a passo comentado está na skill `subir-airflow`; aqui ficam os scripts prontos. Antes, leia `servicos-no-ambiente` (supervisor `rt-service`) e `ambiente-do-cliente` (tarefas longas).

## Arquivos
- `instalar_airflow.sh`: cria o venv em `/opt/airflow-venv`, instala o Airflow com as constraints oficiais (versão do Python detectada), migra o banco e registra os serviços `airflow-web` (porta 8080) e `airflow-scheduler`. É **demorado**: rode em segundo plano com log (`setsid nohup bash instalar_airflow.sh > /workspace/logs/airflow.log 2>&1 < /dev/null &`) e acompanhe com `tail`.
- `dag_exemplo.py`: DAG diária às 8h (Brasília) para copiar para `/workspace/airflow/dags/`.

## Cuidados
- Senha do administrador: o script gera uma senha forte, grava em `/workspace/airflow/admin_senha.txt` com permissão 0600 e imprime só o caminho. Entregue-a ao usuário e apague o arquivo depois.
- Peça ~2 GB de RAM livre; avise se o plano não comportar.
- Publique (`publicar-app`, porta 8080) só depois do administrador criado, e com confirmação do usuário.
