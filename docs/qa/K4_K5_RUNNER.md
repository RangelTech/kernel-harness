# Runner K-4/K-5

O runner `qa/k4_k5_runner.py` produz evidência JSON para carga contínua do
kernel. Ele não faz deploy, não lê cofre, não grava segredos e não mata
containers por conta própria.

## Pré-condições

Execute em ambiente QA, com template publicado que aceite o `model` e produza
uma resposta curta. O processo precisa receber, pelo ambiente (não por linha
de comando), `HARNESS_QA_BASE_URL`, `HARNESS_QA_SERVICE_TOKEN` e
`HARNESS_QA_RUN_TOKEN_SECRET`. Opcionalmente configure
`HARNESS_QA_TEMPLATE_ID`, `HARNESS_QA_TEMPLATE_VERSION`, `HARNESS_QA_MODEL_ALIAS`
e `HARNESS_QA_MESSAGE`.

O token de cada run é assinado no formato do backend e expira pouco depois do
timeout. O relatório nunca inclui token, prompt completo, cabeçalhos ou corpo
de resposta.

## K-4: soak

Rode a partir de uma máquina que alcance a API interna, ou dentro de um
container autorizado. Exemplos deliberadamente usam nomes QA:

```bash
python qa/k4_k5_runner.py soak --duration-s 1800 --concurrency 15 --tenants 10 \
  --out /tmp/k4-15.json
python qa/k4_k5_runner.py soak --duration-s 1800 --concurrency 50 --tenants 10 \
  --out /tmp/k4-50.json
```

Aceite: `success_ratio >= 0.95`, p95 de primeiro evento abaixo de 5 s e sem
erros de fila em carga normal. Anexe os dois JSONs ao registro da spec.

## K-5: queda durante run

Primeiro prepare uma instrução de QA que mantenha a run realmente ativa. Rode:

```bash
python qa/k4_k5_runner.py in-flight --timeout-s 900 --ready-timeout-s 120 \
  --expect-interrupted --out /tmp/k5-kill.json --ready-file /tmp/k5-ready.json
```

Quando `k5-ready.json` indicar `first_event_seen: true`, o operador executa
separadamente o `kill -9` no worker escolhido, pelo procedimento oficial de
manutenção. O runner continua lendo o SSE e consulta o estado terminal; passa
somente se a run terminar como `interrupted` com código `interrupted`.

Depois execute uma nova run QA curta e registre que ela concluiu. Isso prova a
recuperação sem esconder a interrupção da run em voo.
