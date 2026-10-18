---
name: pix-mercadopago
description: Criar cobranças Pix (QR e copia-e-cola) e consultar o status de pagamentos pelo Mercado Pago da empresa.
---

# Pix com Mercado Pago

Ferramentas: `rt_pix_create`, `rt_payment_status`, `rt_mp_read`.

## Criar uma cobrança
1. Reúna **valor** (em reais, até 2 casas decimais, ex.: `10.50`), **descrição** e uma **referência estável** do pedido (a mesma referência no mesmo chat nunca gera cobrança duplicada).
2. Siga a skill `boas-praticas-de-confirmacao`: a plataforma pede a confirmação do usuário antes de criar a cobrança.
3. Chame `rt_pix_create` com `amount`, `description` e `reference`.
4. Entregue ao usuário o QR/copia-e-cola retornado e diga como acompanhar o pagamento.

## Acompanhar
- Use `rt_payment_status` com a referência ou o id do pagamento. Status comuns: pendente, aprovado, recusado, expirado.
- Só diga que o pagamento foi recebido quando o status for **aprovado**.

## Cuidados
- Nunca arredonde valores nem converta moeda por conta própria.
- Se o limite de valor for excedido a ferramenta recusa; informe o usuário e não tente dividir a cobrança para contornar.
