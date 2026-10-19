---
name: exemplo-http-api-generica
description: Exemplos de cliente para qualquer API REST em Python (requests): autenticação Bearer, paginação, retry com espera e OAuth client credentials.
---

# Exemplo: API HTTP genérica

Referência, não obrigação. Para uma chamada simples, `rt_http` com `{{secret:NOME}}` basta.

Segredos (em `secrets`), conforme a API: `API_TOKEN` (Bearer), ou `OAUTH_CLIENT_ID` + `OAUTH_CLIENT_SECRET` + `OAUTH_TOKEN_URL`. A base da API é passada por argumento.

## Arquivo
- `api_generica.py`: `sessao()` (Bearer + timeout + User-Agent), `com_retry()` (backoff exponencial para 429/5xx, respeita `Retry-After`), `paginar()` (páginas com `page`/`per_page` ou cursor `next`), `token_oauth()` (client credentials). Rodar o arquivo mostra o retry contra um servidor local.

## Cuidados
- Sempre `timeout`. Repita só chamadas seguras (GET, ou POST com chave de idempotência).
- Máximo de tentativas fixo; não repita em laço infinito.
- Respeite limites e termos da API; leia o corpo do erro antes de tentar de novo.
- Nunca imprima o cabeçalho `Authorization`.
