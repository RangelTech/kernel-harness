---
name: exemplo-docker-no-ambiente
description: Como verificar se o ambiente permite Docker, instalar e rodar contêineres como serviço supervisionado, com alternativa quando não for possível.
---

# Exemplo: Docker no ambiente

Referência, não obrigação. O ambiente já é um contêiner; Docker dentro dele **pode não funcionar** (depende do plano e do isolamento). Teste antes de prometer.

## Arquivo
- `docker_exemplo.sh`: `verificar` (diz se o Docker está utilizável), `instalar` (`docker.io` pelo apt), `rodar NOME IMAGEM PORTA_HOST:PORTA_CONT [args...]` (registra um serviço `rt-service` que executa `docker run` em primeiro plano).

## Receita
1. `bash docker_exemplo.sh verificar`. Se disser `indisponivel`, **não insista**: use binário/jar/venv com `rt-service` (ver `exemplo-metabase-no-ambiente`, modo `jar`) e conte ao usuário o motivo.
2. Se faltar o Docker mas o ambiente permitir: `instalar` (em segundo plano com log).
3. `rodar` registra o contêiner como serviço; acompanhe com `rt-service logs NOME 50`.

## Cuidados
- Sem `-d` no `docker run` dentro do serviço: o supervisor precisa do processo em primeiro plano.
- Volumes em `/workspace/...` para os dados sobreviverem.
- Não monte o socket do Docker do host nem use `--privileged`.
