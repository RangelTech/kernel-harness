---
name: servicos-no-ambiente
description: Subir, desligar, reiniciar e acompanhar serviços (programas de longa duração) no ambiente da empresa com o supervisor rt-service.
---

# Serviços supervisionados

Todo programa que deve ficar rodando (site, API, Airflow, Metabase, worker) é um **serviço** do supervisor `rt-service`. Ele reinicia sozinho se cair e volta sozinho depois que o ambiente reinicia.

## Comandos (no terminal do ambiente)
- Criar/atualizar: `rt-service add NOME [--user ws|root] [--cwd DIR] [--env K=V]... -- COMANDO ARGS...`
  - Nome: minúsculas, números, `-` ou `_`, até 31 caracteres, começando por letra.
  - Padrão: roda como `ws` em `/workspace`. Use `root` só se for realmente necessário.
- Estado: `rt-service status NOME` e `rt-service list`
- Ligar/desligar/reiniciar: `rt-service start|stop|restart NOME`
- Logs: `rt-service logs NOME 200`
- Remover: `rt-service rm NOME`

## Receita
1. Teste o comando antes (em primeiro plano, com `timeout 10 ...`).
2. Defina o serviço com `rt-service add`; ele já sobe.
3. Confirme com `rt-service status` e olhe `rt-service logs`.
4. Para expor na internet, siga a skill `publicar-app` (o serviço deve escutar em `0.0.0.0:<porta>`).

## Desligar / ligar a pedido do usuário
- "Desligue o X": `rt-service stop X` e confirme com `status` (`down`). Isso não apaga nada.
- "Ligue o X": `rt-service start X`.
- "Apague o X": peça confirmação, depois `rt-service rm X`; os dados em `/workspace` continuam.

## Boas práticas
- Variáveis de ambiente por `--env`, nunca segredos em texto no comando.
- Um serviço por responsabilidade; logs ficam em `/var/lib/rt/log/NOME.log` (rotacionam sozinhos).
