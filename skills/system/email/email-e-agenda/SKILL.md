---
name: email-e-agenda
description: Ler a caixa de entrada e enviar e-mails pelas contas da empresa (IMAP/SMTP), sempre com confirmação antes do envio.
---

# E-mail

Ferramentas: `rt_email_list`, `rt_email_send`.

- **Listar**: `rt_email_list` com o rótulo da conta (`account`), a pasta e um limite pequeno. Resuma remetente, assunto e urgência.
- **Enviar**: redija o texto, mostre ao usuário (destinatários, assunto, corpo) e chame `rt_email_send`. A plataforma exige o cartão de confirmação; sem a aprovação nada é enviado.
- Não inclua segredos nem dados que o usuário não tenha pedido.
- Para anexos e agenda, informe que ainda não estão disponíveis neste agente, em vez de improvisar.
