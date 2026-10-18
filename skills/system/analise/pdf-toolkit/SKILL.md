---
name: pdf-toolkit
description: Ler, juntar, dividir, preencher e gerar arquivos PDF (relatórios, propostas, recibos) e entregá-los na conversa.
---

# PDF

Use o executor isolado (`rt_executor_run`) com Python (`pypdf`, `reportlab`).

- **Ler**: extraia o texto e as tabelas; resuma e cite a página.
- **Juntar/dividir**: preserve a ordem pedida e confira o número de páginas no resultado.
- **Gerar**: monte título, corpo e rodapé com a identidade da empresa; use fonte embutida e A4.
- **Preencher**: só campos que o usuário informou; nunca invente dados pessoais ou valores.
- **Entregar**: `rt_artifact_put` com `kind=pdf` e nome claro (`proposta-cliente-2026-10.pdf`). Informe tamanho e número de páginas.
- Dados sensíveis (CPF, cartão) só se necessários; mostre ao usuário antes de gravar no arquivo.
