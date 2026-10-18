---
name: cobranca-e-conciliacao
description: Cobrar clientes (Pix) e conferir quais cobranças foram pagas, com relatório de pendências.
---

# Cobrança e conciliação

1. Para **cobrar**, use a skill `pix-mercadopago` (referência estável por pedido; valor confirmado com o usuário).
2. Para **conciliar**: liste as referências abertas (`rt_sql_read` na tabela de pedidos, quando houver) e consulte cada uma com `rt_payment_status`. Agrupe: pagas, pendentes, expiradas, recusadas.
3. Apresente um resumo (quantidade e valor total por grupo) e uma lista curta das pendências mais antigas.
4. Para lembrar o cliente, redija a mensagem e peça confirmação antes de enviar (`email-e-agenda` ou o canal da conversa).
5. Nunca marque como pago sem o status **aprovado** vindo do provedor.
