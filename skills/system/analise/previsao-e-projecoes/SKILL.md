---
name: previsao-e-projecoes
description: Projetar vendas e resultados do mês a partir do histórico, com método simples, intervalo de incerteza e premissas claras.
---

# Projeções

1. **Colete o histórico** com `rt_sql_read` (vendas por dia/semana, produto, canal; pelo menos 8 semanas, se existirem).
2. **Método** (no executor, `rt_executor_run`): comece simples e explicável.
   - Run-rate do mês: vendido até hoje ÷ dias decorridos × dias do mês.
   - Ajuste por sazonalidade semanal (dia da semana) quando houver histórico.
   - Tendência linear ou média móvel de 4 semanas para comparar.
3. **Mostre sempre**: realizado até agora, projeção (valor esperado) e faixa pessimista/otimista, **premissas** e **limitações** (poucos dados, campanha pontual, feriados).
4. Gere um gráfico (`gerar-graficos-e-relatorios`): realizado em barras, projeção em linha tracejada, faixa sombreada.
5. **Nunca** apresente a projeção como promessa. Diga o que mudaria o resultado e ofereça um cenário alternativo (ex.: "se o ticket médio subir 10%").
6. Cálculos de dinheiro com `Decimal`/2 casas; não misture o realizado com o projetado.
