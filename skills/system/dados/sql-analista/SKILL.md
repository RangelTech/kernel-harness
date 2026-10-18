---
name: sql-analista
description: Consultar e (com confirmação) alterar dados das fontes de dados da empresa com SQL seguro, e explicar os resultados.
---

# Analista de SQL

Ferramentas: `rt_data_describe`, `rt_sql_read`, `rt_mongo_find`, `rt_sql_write`, `rt_sql_transaction`.

## Leitura
1. Comece com `rt_data_describe` para ver tabelas e colunas disponíveis; não adivinhe nomes.
2. Use `rt_sql_read` com **um único** `SELECT`/`WITH`. Use `LIMIT` e filtros; agregue no banco quando possível.
3. Resuma os achados em linguagem de negócio e mostre os números principais. Se houver muitas linhas, apresente os totais e pontos de atenção em vez de despejar tudo.

## Escrita
- Use `rt_sql_write` / `rt_sql_transaction` apenas quando o usuário pedir explicitamente uma alteração e **após a confirmação** (skill `boas-praticas-de-confirmacao`).
- Antes, rode um `SELECT` equivalente para mostrar quais linhas serão afetadas.
- Nunca use `DROP`, `TRUNCATE` ou alterações de estrutura.

## Cuidados
- Não exponha dados pessoais além do necessário para a pergunta.
- Se a consulta falhar, leia a mensagem, corrija e tente novamente no máximo duas vezes antes de pedir ajuda ao usuário.
