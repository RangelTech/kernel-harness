# contracts (v1)

JSON Schemas (draft 2020-12) do contrato Backend -> kernel-harness (spec 04, secao 4) e da
interface plugin -> broker. `contract_version: 1`. Os testes de contrato
(`tests/test_contracts.py`) validam exemplos validos/invalidos e tudo o que a propria API
emite; o backend deve copiar estes arquivos para os testes dele.

| Arquivo | Uso |
|---|---|
| `v1/run_request.schema.json` | corpo de `POST /v1/runs` (credenciais em claro, so memoria da run) |
| `v1/sse_event.schema.json` | cada evento da resposta SSE (`event:` = `type`, `data:` = JSON) |
| `v1/error_response.schema.json` | erros HTTP antes do stream (401/403/409/422/429) |
| `v1/cancel.schema.json`, `v1/approval.schema.json` | cancelar run; decisao autenticada |
| `v1/broker_call.schema.json` | `POST /broker/v1/call` `{session_id, tool, args}` |
| `v1/skills_validate.schema.json` | `POST /v1/skills/validate` |

Eventos de progresso do stream (somente para quem tem stream, ou seja, o chat web):

- `thinking` `{text}`: resumo curto do raciocinio (sem nomes internos), no maximo 400 caracteres.
- `step` `{kind,label,status}`: ferramenta/habilidade em linguagem amigavel.
- `agent` `{name,status}` e `subagents` `{active,total}`: subagentes em tempo real. O total
  soma o tamanho de cada lote de delegacao; `active` cai quando o filho termina ou a delegacao acaba.
- `done` `{text,format}`: `format` (`markdown` | `whatsapp` | `text`) vem de
  `manifest.output_format` do pedido; o kernel tambem instrui o agente a escrever nesse formato.

Canais externos nao tem stream: o consumidor (ponte) usa so o `done`/`artifact`.

Eventos desconhecidos devem ser ignorados pelo consumidor. Mudanca incompativel = `v2/`.
