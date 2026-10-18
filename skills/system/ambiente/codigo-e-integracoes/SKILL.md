---
name: codigo-e-integracoes
description: Escrever e rodar código no ambiente da empresa para integrar APIs (Mercado Pago, CRMs, planilhas), usando segredos como variáveis de ambiente.
---

# Código e integrações

Quando a tarefa não é coberta por uma ferramenta pronta, **programe**. O terminal do ambiente (`rt_env_exec`) tem internet, Python e pastas persistentes.

## Receita
1. Veja o que existe: `rt_env_status`; inicie com `rt_env_start` se preciso.
2. Escreva o script em `/workspace/<projeto>/` (Python com `requests` ou `urllib`; instale com `pip install --user requests`).
3. **Segredos**: nunca no código. Liste o NOME em `secrets` na chamada do terminal e leia com `os.environ["NOME"]`. Nomes disponíveis: `MERCADO_PAGO_TOKEN` e os segredos cadastrados pela empresa (nome em maiúsculas, não alfanuméricos viram `_`). O valor nunca aparece na resposta.
4. Rode, leia a saída e corrija. Para algo recorrente, deixe como serviço (`servicos-no-ambiente`) ou rotina (`orquestracao-e-rotinas`).

## Exemplo: Pix pela API do Mercado Pago (quando quiser controle total)
```python
import os, uuid, requests
h = {"Authorization": "Bearer " + os.environ["MERCADO_PAGO_TOKEN"], "X-Idempotency-Key": str(uuid.uuid4())}
r = requests.post("https://api.mercadopago.com/v1/payments", headers=h, json={
    "transaction_amount": 9.90, "description": "Guia", "payment_method_id": "pix",
    "payer": {"email": "cliente@example.com"}, "external_reference": "PED-123"}, timeout=30)
d = r.json(); print(d["id"], d["status"], d["point_of_interaction"]["transaction_data"]["qr_code"])
```
Consultar: `GET /v1/payments/<id>` (status `approved` = pago). As ferramentas `rt_pix_create` e `rt_payment_status` fazem o mesmo e já registram a cobrança na plataforma; prefira-as quando bastarem.

## Cuidados
- Imprima só o necessário; nunca imprima o token.
- Respeite limites e termos das APIs de terceiros; trate erros (status, mensagem) e não repita em laço.
- Entregue ao usuário o resultado em português simples (QR, código copia-e-cola, link), não o JSON cru.
