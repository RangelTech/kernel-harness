---
name: exemplo-sql
description: Exemplos de código SQL em Python (sqlite, PostgreSQL com psycopg, MySQL com pymysql): leitura, escrita com transação e parâmetros.
---

# Exemplo: SQL no ambiente

Referência, não obrigação. Para consultas simples em fontes cadastradas, prefira `rt_sql_read`/`rt_sql_write`.

Segredos (em `secrets`): `DATABASE_URL` (PostgreSQL, ex.: `postgresql://{usuario}:{senha}@{host}/{banco}`) ou `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASS` (MySQL).

## Arquivo
- `sql_exemplos.py`: funções de leitura e de escrita em transação para sqlite (stdlib), PostgreSQL (`pip install --user "psycopg[binary]"`) e MySQL (`pip install --user pymysql`). Rodar o arquivo faz uma demonstração em sqlite temporário.

## Regras
- Sempre parâmetros (`?` no sqlite, `%s` no PostgreSQL/MySQL), nunca concatenar texto do usuário no SQL.
- Escrita dentro de transação: tudo ou nada.
- Leia antes de escrever (`SELECT count(*)` com o mesmo filtro) e conte ao usuário quantas linhas mudaram.
- `DELETE`/`UPDATE` sem `WHERE` ou em muitas linhas: confirme na conversa.
