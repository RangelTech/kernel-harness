# luar_harness

Executa os agentes por skills. Recebe uma run do motor, coloca na fila, entrega a um worker Hermes isolado por empresa e devolve eventos em tempo real (SSE) até um estado final. O Hermes Agent (NousResearch, MIT) entra como imagem fixada, não como cópia.

> Nome anterior: `kernel-harness`.

## Onde ele se encaixa

```
                    Cliente (navegador e canais)
                                |
                        Traefik (VPS)
                                |
 luar_chatwoot --contexto assinado--> luar_engine --runs--> luar_harness
 (telas, inbox, Kanban,               (motor de IA:         (fila, broker,
  bridge, gateway de canais)           templates, skills,    workers Hermes)
                                       credenciais, uso)            |
                                            |                luar_workspaces
                                       luar_ai_gateway       (ambiente de cada
                                       (LLM multi-tenant)     empresa)

 vps_rt_infra: compose, releases por digest, deploy e backup (só ela implanta)
 airflow:      orquestração da empresa, inclui o backup noturno da VPS
 luar_docs:    specs, evidências e ADRs
```

## Árvore de pastas

```
harness/    API interna, fila (Redis Streams), dispatcher, tradução de eventos, recuperação de runs
broker/     ferramentas rt_*: credenciais só na memória da run; SQL, Pix, e-mail e HTTP protegidos
runtime/    gerenciador de runtimes por empresa
executor/   ferramentas e jobs isolados
skills/     skills oficiais de sistema (SKILL.md)
contracts/  contrato Motor -> Harness e eventos, versionados
plugin/     plugin do broker e imagem do Hermes
dev/        pilha local com Hermes e backend falsos, e smoke.py
tests/      pytest offline (contrato, broker, fila, skills)
docs/       ADRs, runbooks e guia para escrever agente por skills
```

## Como rodar

```bash
pip install -r requirements-dev.txt
docker compose -f dev/compose.harness.yml up --build
python dev/smoke.py "ola"
curl localhost:8080/health
```

## Como testar

`python -m pytest -q` (100% offline) e `scripts/verify.sh` (build e smoke em Docker, repetido no CI).

## Como implantar

Uma tag `v*` publica a imagem por digest e o manifesto. Só o `vps_rt_infra` implanta. Antes de trocar o worker em produção, drene as runs em andamento (regra K-10).

## Contratos e segurança

- O contrato fica em `contracts/v1/`; mudança de campo é versionada.
- Credenciais existem só na memória da run: nunca em log, evento ou disco.
- Cada empresa tem seu ambiente e seus limites; o broker nega por padrão.

## Como contribuir

Uma branch por tarefa, Conventional Commits em português, CI verde. Leia `docs/adr/` antes de mexer em fronteiras.
