---
name: orquestracao-e-rotinas
description: Criar rotinas recorrentes (ex.: e-mail diário às 8h) e fluxos de trabalho, escolhendo entre agendamento da plataforma e Airflow no ambiente.
---

# Rotinas e orquestração

## Escolha
- **Aviso, e-mail, relatório simples, lembrete** → agendamento da plataforma (`rt_schedule_create`). É o caminho padrão: a plataforma acorda o agente no horário, com as permissões e as ferramentas certas (inclusive e-mail e Pix, que exigem credenciais que **não** ficam no ambiente).
- **Pipeline de dados, vários passos, dependências, reprocessamento** → Airflow no ambiente (`subir-airflow`), com os DAGs em `/workspace/airflow/dags`.

## Agendamento da plataforma
1. Entenda: o que fazer, para quem, quando (dias e horário, fuso de Brasília por padrão).
2. Escreva um **texto de instrução** completo que o próprio agente executará a cada disparo (ex.: "Liste os usuários ativos e envie a cada um o resumo do dia por e-mail").
3. Mostre ao usuário o resumo (instrução, destinatários, horário) e **peça confirmação** — envio em massa é uma ação sensível (`boas-praticas-de-confirmacao`).
4. `rt_schedule_create` com `name`, `cron` (ex.: `0 8 * * *`), `timezone` e `instruction`.
5. Confirme com `rt_schedule_list`. Para pausar/apagar: `rt_schedule_pause` / `rt_schedule_delete`.
6. Cada disparo gera uma conversa registrada; diga ao usuário onde acompanhar.

## Envio em massa
- Antes de agendar, valide a lista de destinatários e inclua forma de descadastro quando o conteúdo for promocional.
- Limite o volume por disparo e avise se a lista for muito grande.
