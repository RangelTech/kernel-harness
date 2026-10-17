# kernel-harness

Kernel de IA do RAgent: roda o **Hermes Agent** (NousResearch, projeto externo) num runtime isolado por tenant e expõe ao backend (`agent-platform`) uma API interna com eventos SSE e estados terminais. **Substitui o `kernel-llm`** (LangGraph com supervisor + especialistas).

> Estado: **scaffold** (spec 01 do plano de reorganização). O comportamento funcional entra na spec 04.

## O que este repo é (e não é)
| É | Não é |
|---|---|
| adaptador Hermes, gerenciador de runtimes por tenant, executor de ferramentas, broker de credenciais, contrato Backend→Kernel | backend/frontend (isso é o `agent-platform`), infraestrutura da VPS (isso é o `vps_rt_infra`), um fork do Hermes |

## Mapa
```
harness/    serviço do kernel (API interna, adaptador Hermes)         -> imagem ghcr.io/rangeltech/kernel-harness/harness
runtime/    gerenciador de runtimes por tenant (Hermes por tenant)    (spec 04)
executor/   ferramentas/jobs isolados                                 (spec 04)
broker/     credenciais: valida tenant/template/ator a cada uso       (spec 04/06)
contracts/  contrato Backend->Kernel e eventos (versionados)
skills/     skills oficiais de sistema (SKILL.md)
docker/     Dockerfiles
scripts/    verify.sh (gate local)
docs/       adr/, runbooks/, qa/, provenance.md
```

## Regras de entrega (valem para todo repo da Rangel Tech)
1. **Este repo produz e publica artefatos; ele nunca faz deploy.** Só o repo `RangelTech/vps_rt_infra` tem as credenciais da VPS e implanta.
2. Todo release publica a imagem **por digest** (`ghcr.io/rangeltech/kernel-harness/harness@sha256:...`) e um `release-manifest.json` como asset da GitHub Release. O `vps_rt_infra` lê o manifesto, fixa o digest em `releases/kernel-harness.lock` e faz o deploy.
3. **Gate local obrigatório** antes de publicar: `scripts/verify.sh` (sobe a mesma imagem em Docker e roda o smoke). O CI repete o mesmo comando.
4. **Repo público:** nada de segredo, `.env`, dado de cliente ou IP/credencial de produção entra aqui. `gitleaks` roda em todo PR.

## Desenvolvimento local
```bash
scripts/verify.sh            # build + smoke (precisa de Docker)
docker compose -f compose.dev.yml up --build
curl localhost:8080/health
```

## Release
```bash
git tag v0.1.0 && git push origin v0.1.0     # dispara .github/workflows/release.yml
```

## Licenças e atribuição
O Hermes Agent é MIT (NousResearch) e é consumido por imagem fixada, não copiado.
