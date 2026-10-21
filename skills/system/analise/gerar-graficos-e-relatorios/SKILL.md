---
name: gerar-graficos-e-relatorios
description: Gerar gráficos, tabelas e relatórios (imagem, planilha, PDF) a partir de dados e entregá-los na conversa.
---

# Gráficos e relatórios

1. **Dados**: obtenha-os com `rt_sql_read` (ou do que o usuário enviou). Traga só as colunas e linhas necessárias, já agregadas.
2. **Cálculo e gráfico**: use o executor de código isolado (`rt_executor_run`) com Python (`pandas`, `matplotlib`). Salve a figura em PNG (largura ~1000 px, fonte legível, título, eixos com unidade e legenda).
3. **Entrega**: registre o arquivo com `rt_artifact_put` (`kind=image` para gráfico, `kind=xlsx` para planilha, `kind=pdf` para relatório). O chat mostra a imagem ou o link de download.
4. **Explique**: depois do gráfico, escreva 2–4 frases com o que ele mostra, o período, a fonte e qualquer ressalva.

## Boas práticas
- Um gráfico por ideia; evite pizza com muitas fatias; ordene barras.
- Moeda em R$ com separador brasileiro; datas `dd/mm/aaaa`.
- Mostre os números-chave também em texto (acessibilidade).
- Se faltarem dados para o período pedido, diga qual período foi possível.

## Planilhas .xlsx (openpyxl)
- Nomes de função nas fórmulas SEMPRE em inglês (`SUM`, `SUMIF`, `IF`, `VLOOKUP`), mesmo com o usuário falando português: o arquivo guarda o nome em inglês e o Excel traduz ao abrir. `SOMA`/`SOMASE` gravados pelo openpyxl dão `#NAME?`. Separador de argumentos é vírgula.
- O openpyxl não calcula: confira os totais em Python (pandas) e cite-os na resposta.
