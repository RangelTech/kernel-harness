---
name: exemplo-pix-mercadopago
description: Exemplos de código para criar cobrança Pix no Mercado Pago (QR e copia-e-cola) e consultar o status, em Python (urllib) e curl.
---

# Exemplo: Pix com Mercado Pago

Referência, não obrigação. Se as ferramentas `rt_pix_create` e `rt_payment_status` estiverem disponíveis e bastarem, prefira-as (registram a cobrança na plataforma). Estes scripts servem quando você precisa de controle total.

Segredo: liste `MP_ACCESS_TOKEN` (ou `MERCADO_PAGO_TOKEN`) em `secrets` no `rt_env_exec`; o script lê da variável de ambiente.

## Arquivos
- `criar_pix.py VALOR "descrição" REFERENCIA [email]`: cria o Pix, imprime id, status e copia-e-cola, e grava o QR em `/workspace/out/pix-<ref>.png`. A chave de idempotência é derivada da referência: repetir o comando com a mesma referência não duplica a cobrança.
- `consultar_pix.py ID`: imprime o status (`approved` = pago).
- `criar_pix.sh VALOR "descrição" REFERENCIA`: o mesmo com curl.

## Cuidados
- Valor com `Decimal`, duas casas. Teto de segurança: variável `PIX_TETO` (padrão 500,00); acima disso o script recusa.
- Pedido claro do usuário com valor dito por ele = execute. Valor que o usuário não citou = confirme na conversa.
- Só diga "pago" quando o status for `approved`. Nunca imprima o token.
- Entregue o copia-e-cola e o PNG do QR (`rt_env_download`).
