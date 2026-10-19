---
name: exemplo-tarefa-longa-no-ambiente
description: Como rodar uma tarefa demorada no ambiente (segundo plano com log, rt-service para o que deve ficar de pé), acompanhar por polling e devolver o resultado.
---

# Exemplo: tarefa longa no ambiente

Referência, não obrigação. Cada resposta sua tem limite de tempo (~9 min); nunca espere uma tarefa longa no mesmo comando.

## Arquivos
- `tarefa_longa.sh NOME COMANDO...`: inicia em segundo plano (`setsid nohup`), com log em `/workspace/logs/NOME.log`, arquivo `NOME.pid` e marcador `NOME.fim` com o código de saída quando terminar.
- `acompanhar.sh NOME`: mostra `rodando`, `concluida` ou `falhou (código N)` e as últimas linhas do log. Chame a cada poucos minutos, em chamadas curtas.
- `tarefa_longa.py`: o mesmo padrão em Python (função `iniciar` e `estado`).

## Quando usar o quê
- Termina sozinha (instalação, download, build, relatório pesado): `tarefa_longa.sh`.
- Deve ficar rodando e voltar após reinício (site, API, worker): `rt-service add` (skill `servicos-no-ambiente`), nunca `nohup` solto.
- Recorrente em horário: rotina da plataforma ou Airflow (`orquestracao-e-rotinas`).

## Devolver o resultado
1. Só passe adiante quando `acompanhar.sh` disser `concluida` e o arquivo esperado existir (confira tamanho).
2. Entregue com `rt_env_download` (arquivo) ou resuma o log em linguagem simples.
3. Se `falhou`, leia o fim do log, corrija e rode de novo no máximo duas vezes; depois diga ao usuário o que falta.
4. Avise o usuário no começo ("isso leva uns minutos, volto com o resultado") e não invente progresso.
