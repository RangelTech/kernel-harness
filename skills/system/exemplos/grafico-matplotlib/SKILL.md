---
name: exemplo-grafico-matplotlib
description: Exemplo de gráfico com matplotlib no ambiente (sem tela): barras e linha salvas em PNG em /workspace/out e entregues ao usuário.
---

# Exemplo: gráfico com matplotlib

Referência, não obrigação. Instalação: `pip install --user matplotlib`.

## Arquivo
- `grafico.py [SAIDA.png]`: barras com rótulos e linha de tendência. Usa o backend `Agg` (sem janela), título e eixos em português, `dpi=150`. Saída padrão: `/workspace/out/grafico.png` (ou `$OUT_DIR`).

## Entrega
Depois de gerar, chame `rt_env_download` com o caminho do PNG; o usuário recebe o link. Descreva em uma frase o que o gráfico mostra.

## Cuidados
- Sempre `matplotlib.use("Agg")` antes de importar `pyplot`; o ambiente não tem tela.
- Sempre `plt.close(fig)` ao terminar.
- Rotule eixos e unidades; valores monetários como `R$ 1.234,50`.
- Use os dados reais da consulta, não invente números.
