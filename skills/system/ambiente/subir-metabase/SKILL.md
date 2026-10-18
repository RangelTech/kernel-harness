---
name: subir-metabase
description: Instalar o Metabase (BI) no ambiente da empresa, deixá-lo rodando como serviço e publicar o endereço.
---

# Metabase no ambiente

1. Estado do ambiente: `rt_env_status` (inicie se precisar).
2. Instalação **em segundo plano** (veja `ambiente-do-cliente`, "Tarefas longas"): Java e o programa juntos, com log:
   `mkdir -p /workspace/logs /opt/metabase && setsid nohup bash -c 'apt-get update && apt-get install -y default-jre-headless && curl -fsSL -o /opt/metabase/metabase.jar https://downloads.metabase.com/latest/metabase.jar && echo INSTALADO' > /workspace/logs/metabase-install.log 2>&1 < /dev/null & echo iniciado`
3. Acompanhe com `tail -n 5 /workspace/logs/metabase-install.log` até aparecer `INSTALADO` (confira o tamanho do .jar: dezenas de MB).
4. Dados do próprio Metabase em disco persistente: `mkdir -p /workspace/metabase-data`.
5. Serviço:
   `rt-service add metabase --cwd /opt/metabase --env MB_DB_FILE=/workspace/metabase-data/metabase.db --env MB_JETTY_PORT=3000 --env MB_JETTY_HOST=0.0.0.0 -- java -Xmx1g -jar /opt/metabase/metabase.jar`
6. A primeira subida demora (1–3 min). Acompanhe com `rt-service logs metabase 50` até aparecer "Metabase Initialization COMPLETE" e teste `curl -s localhost:3000/api/health`.
7. **Antes de publicar**, crie o administrador na primeira tela (o Metabase pede) com senha forte e entregue as credenciais **só ao usuário**, na conversa.
8. Publicar: skill `publicar-app` → `rt_env_expose` com `app=metabase` e `port=3000`.
9. Conectar dados: no Metabase, adicione a fonte de dados da empresa usando as credenciais que o usuário informar por ele mesmo na tela do Metabase; não copie segredos para arquivos.

## Cuidados
- Reserve ~1 GB de RAM para o Metabase; se o plano for pequeno, avise.
- Para desligar: `rt-service stop metabase`.
