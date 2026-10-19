---
name: exemplo-metabase-no-ambiente
description: Script para instalar o Metabase no ambiente, por jar com Java ou por Docker conforme o que o ambiente permitir, rodando com rt-service.
---

# Exemplo: Metabase no ambiente

Referência, não obrigação. O passo a passo está em `subir-metabase`; aqui ficam os scripts.

## Arquivo
- `instalar_metabase.sh [jar|docker]`: sem argumento, escolhe `docker` se `docker info` funcionar, senão `jar`.
  - `jar`: instala `default-jre-headless`, baixa `metabase.jar` para `/opt/metabase` e registra o serviço `metabase` (porta 3000, dados em `/workspace/metabase-data`).
  - `docker`: sobe o contêiner `metabase/metabase` com volume em `/workspace/metabase-data`, também supervisionado por `rt-service` (o serviço executa `docker run` em primeiro plano, para o supervisor reiniciar).

## Receita
1. Rode em segundo plano com log (`ambiente-do-cliente`, "Tarefas longas"): `setsid nohup bash instalar_metabase.sh > /workspace/logs/metabase.log 2>&1 < /dev/null &`.
2. A primeira subida leva 1 a 3 minutos: `curl -s localhost:3000/api/health` até `ok`.
3. O usuário cria o administrador na primeira tela, com senha forte dele. Só depois publique (`publicar-app`, porta 3000), com confirmação.

## Cuidados
- ~1 GB de RAM; avise se o plano não comportar.
- Fontes de dados: o próprio usuário informa as credenciais na tela do Metabase; não copie segredos para arquivos.
