---
name: ambiente-do-cliente
description: Como usar o ambiente (servidor) da empresa para instalar programas, guardar arquivos e rodar serviços que sobrevivem a reinícios.
---

# Ambiente da empresa

O ambiente é um servidor Linux (Debian) só da empresa, ligado ao agente pelo terminal. Use-o quando a tarefa exigir instalar software, rodar scripts longos, guardar dados ou manter um serviço no ar.

## O que persiste
- `/workspace` (trabalho), `/home/ws` (conta ws e home do administrador), `/opt` (programas instalados) e `/var/lib/rt` (serviços e logs) **sobrevivem** a parar/iniciar e a atualizações.
- Pacotes do sistema instalados com `apt-get install` ficam registrados e voltam após uma recriação.
- Todo o resto do disco do contêiner é descartável.

## Como trabalhar
1. Verifique o estado com `rt_env_status`. Se estiver parado, use `rt_env_start` e espere ficar `running`.
2. No terminal você entra como administrador; serviços de longa duração rodam como o usuário comum `ws`.
3. Instale com `apt-get install -y <pacote>`, `pip install --user` ou baixando binários para `/opt`. Prefira ambientes virtuais Python em `/workspace/<projeto>/.venv`.
4. **Nunca** use `nohup ... &` nem deixe processo solto: ele morre no reinício. Use a skill `servicos-no-ambiente`.
5. Para o mundo externo enxergar algo, use a skill `publicar-app`. O ambiente não tem IP público próprio.

## Regras
- Não guarde senhas, chaves ou tokens em arquivos do ambiente; use `rt_http` com `{{secret:NOME}}` e as ferramentas da plataforma, que mantêm os segredos fora do ambiente.
- Respeite os limites de CPU, memória e disco do plano; se faltar recurso, avise o usuário em vez de insistir.
- Faça backup (`rt_env_backup`) antes de mudanças grandes ou destrutivas.
- Diga ao usuário, em linguagem simples, o que foi instalado e onde.
