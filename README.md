# kernel-harness

Kernel de IA do RAgent: roda o **Hermes Agent** (NousResearch, projeto externo) num runtime isolado por tenant e expõe ao backend (`agent-platform`) uma API interna com eventos SSE e estados terminais. **Convive com o `kernel-llm`** (LangGraph): o backend escolhe o kernel pelo tipo do template (spec 04, D-50).

> Estado: **núcleo implementado** (spec 04, WP-E): API FastAPI, fila Redis Streams + dispatchers, tradução de eventos, broker `rt_*`, promotor de skills, `/compress`, recuperação de runs. Testes offline com Hermes falso. Relatório do pacote: `specs/anexos/04/exec/WP-E_kernel.md` (repo de specs).

## O que este repo é (e não é)
| É | Não é |
|---|---|
| adaptador Hermes, gerenciador de runtimes por tenant, executor de ferramentas, broker de credenciais, contrato Backend→Kernel | backend/frontend (isso é o `agent-platform`), infraestrutura da VPS (isso é o `vps_rt_infra`), um fork do Hermes |

## Mapa
```
harness/    serviço do kernel (API interna, adaptador Hermes)         -> imagem ghcr.io/rangeltech/kernel-harness/harness
runtime/    gerenciador de runtimes por tenant (Hermes por tenant)    (spec 04)
executor/   ferramentas/jobs isolados                                 (spec 04)
broker/     ferramentas rt_*: credenciais so em memoria da run, SQL/Pix/e-mail/HTTP guardados
devtools/   Hermes FALSO, backend FALSO e externos falsos (so teste/dev; alvo `dev` do Dockerfile)
dev/        compose.harness.yml (Redis + kernel + 2 Hermes falsos + backend falso) e smoke.py
tests/      pytest offline: C (contrato), B (broker), P (fila/pool), S (skills)
contracts/  contrato Backend->Kernel e eventos (versionados)
skills/     skills oficiais de sistema (SKILL.md)
docker/     Dockerfiles
scripts/    verify.sh (gate local)
docs/       adr/, runbooks/ (OPERACAO_KERNEL.md), qa/, provenance.md, ONBOARDING_SERVICO.md, GUIA_ESCREVER_AGENTE_POR_SKILLS.md
```

## Regras de entrega (valem para todo repo da Rangel Tech)
1. **Este repo produz e publica artefatos; ele nunca faz deploy.** Só o repo `RangelTech/vps_rt_infra` tem as credenciais da VPS e implanta.
2. Todo release publica a imagem **por digest** (`ghcr.io/rangeltech/kernel-harness/harness@sha256:...`) e um `release-manifest.json` como asset da GitHub Release. O `vps_rt_infra` lê o manifesto, fixa o digest em `releases/kernel-harness.lock` e faz o deploy.
3. **Gate local obrigatório** antes de publicar: `scripts/verify.sh` (sobe a mesma imagem em Docker e roda o smoke). O CI repete o mesmo comando.
4. **Repo público:** nada de segredo, `.env`, dado de cliente ou IP/credencial de produção entra aqui. `gitleaks` roda em todo PR.

## Desenvolvimento local
```bash
pip install -r requirements-dev.txt
python -m pytest -q          # suites C, B, P, S, 100% offline (fakeredis + Hermes falso)
scripts/verify.sh            # build + smoke (precisa de Docker)
docker compose -f dev/compose.harness.yml up --build   # pilha completa com fakes
python dev/smoke.py "ola"    # envia uma run e imprime o SSE
curl localhost:8080/health
```

## Release
```bash
git tag v0.1.0 && git push origin v0.1.0     # dispara .github/workflows/release.yml
```

## Licenças e atribuição
O Hermes Agent é MIT (NousResearch) e é consumido por imagem fixada, não copiado.
