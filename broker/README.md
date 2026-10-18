# broker

Ferramentas `rt_*` (spec 04, secao 6). Roda **no processo do kernel**, nunca no worker Hermes.

- `service.py`: autorizacao (token do pool + `session_id` de uma run ATIVA + worker dono da run + ferramenta no manifesto + grants), tetos por turno, truncamento de saida, auditoria sem valores.
- `data.py` + `sqlio.py`: SQL/Mongo guardados (SELECT/WITH, DML unico, WHERE, allowlist de tabelas, transacao atomica); escrita so com confirmacao autenticada.
- `pix.py`: Mercado Pago com `Decimal`, idempotencia por (chat, referencia, valor), teto, confirmacao antes de criar, QR como artefato.
- `email_tool.py`: IMAP/SMTP; envio com confirmacao, limite de destinatarios.
- `http_tool.py`: `rt_http` com allowlist de hosts por tenant, bloqueio de IP interno/metadata, `{{secret:NOME}}` resolvido aqui.
- `artifacts.py`, `misc.py`: artefatos (register-init/complete), busca de documentos, memoria.

Credenciais (D-57): vivem so em `RunContext` (memoria) e sao apagadas no fim da run.
