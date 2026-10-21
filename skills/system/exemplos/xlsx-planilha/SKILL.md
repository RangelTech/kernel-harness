---
name: exemplo-xlsx-planilha
description: Exemplo de planilha Excel com openpyxl: dados, fórmulas, formatação e gráfico nativo, gravada em /workspace/out.
---

# Exemplo: planilha .xlsx

Referência, não obrigação. Instalação: `pip install --user openpyxl`.

## Arquivo
- `planilha.py [SAIDA.xlsx]`: aba "Vendas" com cabeçalho formatado, linhas de dados, coluna de total (`=B2*C2`), soma no rodapé (`=SUM(...)`), formato de moeda e gráfico de barras nativo. Saída padrão `/workspace/out/vendas.xlsx` (ou `$OUT_DIR`).

## Cuidados
- Funções nas fórmulas sempre em inglês (`SUM`, `SUMIF`, `IF`), nunca `SOMA`/`SOMASE`: em português dão `#NAME?` no Excel.
- Fórmulas são gravadas como texto e o Excel calcula ao abrir; o openpyxl não calcula. Se precisar do resultado no código, calcule em Python também.
- Para ler valores calculados de um arquivo existente use `load_workbook(caminho, data_only=True)`.
- Entregue com `rt_env_download`.
