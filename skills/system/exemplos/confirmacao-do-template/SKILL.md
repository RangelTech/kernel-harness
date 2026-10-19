---
name: exemplo-confirmacao-do-template
description: Como usar a ferramenta rt_confirm quando a skill do template mandar confirmar, e como não perguntar quando a skill mandar agir sem perguntas.
---

# Exemplo: confirmação definida pelo template

Quem manda é a **skill do template** (as regras da empresa). Esta página só mostra o mecanismo. Em conflito, a regra do template vence esta página e a `boas-praticas-de-confirmacao`.

## Quando o template manda confirmar
Antes da ação (nunca depois), chame `rt_confirm`:

```json
{"summary": "Enviar o e-mail de cobrança para 38 clientes em atraso", "risk": "high", "topic": "email_em_massa"}
```

- `summary`: uma frase, no que vai acontecer, com números (quantos, quanto, para quem). Até 500 caracteres.
- `risk`: `low`, `medium` ou `high` (dinheiro, envio em massa, apagar dados = `high`).
- `topic`: assunto curto (`pix`, `grafico`, `ssh`...).

O resultado decide:
- Veio `approved: true` → execute **exatamente** o que descreveu no `summary`. Se precisar mudar o plano, chame `rt_confirm` de novo.
- Negado ou erro → **não execute**. Diga ao usuário que não foi feito e ofereça alternativa.
- Faça uma confirmação por ação lógica, não uma por linha de um lote que o `summary` já cobre.

## Quando o template manda não perguntar
Rotinas automáticas, atendimento sem usuário presente ou template que diz "aja sem perguntas": **não chame `rt_confirm`, não faça perguntas de volta**. Decida com bom senso:
- Dentro do que a regra permite, execute.
- Fora do que permite (valor acima do limite, destinatário não previsto), **não faça** e registre o motivo (nota ou resposta final), em vez de perguntar.
- Conte no fim o que fez e o que deixou de fazer.

## Quando o template não diz nada
Vale `boas-praticas-de-confirmacao`: pedido claro = execute; dúvida real ou risco alto = uma frase de confirmação na própria conversa.
