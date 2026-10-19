# exemplos

Biblioteca de trechos que funcionam, para o agente consultar quando for escrever o próprio código no terminal do ambiente da empresa. Cada pasta é uma skill (`SKILL.md`) com scripts `.py`/`.sh` ao lado. São referência, nunca obrigação: adapte, simplifique ou ignore.

Regras de todos os exemplos: credenciais só por variável de ambiente (nome listado em `secrets` no `rt_env_exec`), nada de valor literal, saída em `/workspace/out`.
Os testes ficam em `tests/exemplos/`.
