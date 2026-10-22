# Onboarding: o serviço kernel-harness

Para quem acabou de chegar. Em 15 minutos você deve saber o que este repositório faz, por onde uma mensagem passa, onde mexer e como provar que nada quebrou. Leituras complementares: `README.md` (regras de entrega), `docs/runbooks/OPERACAO_KERNEL.md` (operação na VPS) e `docs/GUIA_ESCREVER_AGENTE_POR_SKILLS.md` (o que o administrador de negócio escreve).

## 1. O que é

O `kernel-harness` é o **motor de agentes por skills** do RAgent. O backend (`agent-platform`) manda um pedido (`POST /v1/runs`); este serviço enfileira, escolhe um worker do **Hermes Agent** (projeto externo da NousResearch, usado por imagem fixada, nunca copiado), acompanha a execução, traduz os eventos para o formato do produto (SSE) e executa as ferramentas sensíveis (Pix, SQL, e-mail, HTTP, ambiente) no **broker**, onde as credenciais da empresa existem.

Convive com o `kernel-llm` (LangGraph): o backend escolhe o motor pelo tipo do template (`graph` ou `skills`). Este repositório só atende `skills`.

O cliente nunca vê as palavras "kernel", "harness" ou "Hermes". Em texto de produto, use "agente" e "ambiente".

## 2. Arquitetura

```
 Usuario / canal (Chatwoot, API)
          |
          v
 +-------------------+   contexto assinado   +--------------------------+
 |  agent-platform   |---------------------->|  kernel-harness  (este)  |
 |  (backend, banco, |   POST /v1/runs       |                          |
 |   credenciais)    |<----------------------|  api.py    rotas + SSE   |
 +-------------------+   SSE: token, step,   |  scheduler fila + justica|
        ^   ^            artifact, usage,     |  dispatcher executa run  |
        |   |            approval_required,   |  workers   afinidade     |
        |   |            done/error           |  broker/   ferramentas   |
        |   |                                 |            rt_* (cred.)  |
        |   +---- /internal/* (cobrancas, ----+  approvals cartao de OK  |
        |         artefatos, aprovacoes,      +---+----------+-----------+
        |         uso, memorias)                  |          |
        |                                         |          | POST /broker/v1/call
        |                                         v          | (token do pool +
        |                            +---------+  |          |  run ativa)
        |                            |  Redis  |  |          |
        |                            | (fila,  |  |   +------+-------------------+
        |                            | eventos,|  |   | Workers Hermes (pool)    |
        |                            | locks)  |  +-->| rt-hermes-w1 / w2        |
        |                            +---------+      | plugin rt-broker (stubs) |
        |                                             | perfil por empresa/      |
        |                                             |   template/usuario       |
        |                                             +---+---------+------------+
        |                                                 |         |
        |                                    modelo (LiteLLM)     SSH (so se a
        |                                                          empresa tem
        v                                                          ambiente)
 +------------------+                                        +-----------------+
 | Postgres, MinIO  |                                        | runtime-manager |
 | (backend)        |<--- ferramentas rt_env_* ---(HMAC)---->| + ambiente da   |
 +------------------+     (broker fala com o RM)             | empresa         |
                                                              +-----------------+
```

Quem é quem:

| Peça | Papel | Onde vive |
|---|---|---|
| `agent-platform` | API do produto, banco, cofre de credenciais, templates e versões. Decide o motor pelo template. | repo `agent-platform` |
| `kernel-harness` | Fila, pool, perfis, tradução de eventos, broker, confirmação, recuperação. | **este repo**, imagem `harness` |
| Worker Hermes | Roda o agente (modelo + skills + sessão). **Sem terminal, sem arquivos locais, sem internet.** Só enxerga as ferramentas `rt_*` do perfil. | imagem `rt-hermes` (Hermes + plugin) |
| Broker | Único lugar onde credenciais são usadas. Roda **no processo do kernel**, nunca no worker. | `broker/` |
| Redis | Fila (stream), estado das runs, eventos SSE, locks por chat, afinidade. | stack da plataforma |
| LiteLLM | Gateway de modelos; chave virtual por empresa. | stack `litellm` |
| `runtime-manager` | Ambiente (servidor) da empresa, publicação de apps, kill switch. O broker fala com ele por HMAC. | repo `runtime-manager` |

## 3. Fluxo de uma mensagem

1. **Entrada.** O backend chama `POST /v1/runs` com token de serviço. O corpo traz tenant, usuário, chat, template e versão, a mensagem, anexos, o **manifesto** (ferramentas, limites), as **credenciais em claro** e um `run_token` (HS256). A API valida contra `contracts/v1/run_request.schema.json`, confere o `run_token`, aplica idempotência (mesma chave e mesmo corpo repete o stream; corpo diferente dá 409) e recusa com 429 se a fila encheu.
2. **Fila.** A run é gravada no Redis (`run:<id>`, sem credenciais) e entra no stream `runs:queue`. As **credenciais ficam só em memória** (`RunContext`) até o fim da run.
3. **Agendamento.** O `scheduler` respeita: uma run por chat (lock Redis), teto por empresa (2), prioridade (canais com cliente final antes do chat interno), expiração de 60 s na fila. Escolhe o worker pela **afinidade** da empresa e reserva um slot.
4. **Perfil.** O `ProfileManager` materializa em disco as skills (pacote da empresa e skills do usuário) e gera o perfil do Hermes (`profiles/profile_config.py`) quando o hash do template muda. O perfil é por (empresa, template, usuário).
5. **Execução.** O `dispatcher` chama o worker (`POST /p/<perfil>/v1/runs`, `Idempotency-Key` = id da run). O corpo leva `AGENTE.md`, memórias recuperadas e um **catálogo de credenciais por rótulo** (nunca valores). As skills **não** vão no corpo; o Hermes as lê das pastas do perfil.
6. **Ferramentas.** Quando o agente chama `rt_*`, o plugin faz `POST /broker/v1/call` ao kernel. O broker confere: token do worker, `session_id` ligado a uma run **ativa**, worker dono da run, ferramenta no manifesto, grants. Para ações sensíveis, pede **confirmação** (só se o template ligou `confirm_actions`). Executa com a credencial da run, devolve resultado sanitizado (valores de segredo trocados por `[REDACTED]`, saída truncada, teto de chamadas por turno).
7. **Eventos.** O `translate` converte o stream do Hermes em eventos do produto (`token`, `step`, `artifact`, `approval_required`, `usage`, `done`, `error`...) publicados em `events:<run_id>`; a API os entrega por SSE (com reconexão por `Last-Event-ID`).
8. **Fim.** Estado terminal único (`completed`, `failed`, `cancelled`, `interrupted`, `expired`). O kernel libera lock e slot, **apaga as credenciais da memória**, varre `pending/` do perfil (skills e memórias aprendidas viram propostas `pending_review` no backend), registra uso e, se o contexto passou do limiar, manda compactar a sessão.
9. **Falha.** Worker caiu: runs em voo viram `interrupted` (nunca sucesso) e as que não começaram são reatribuídas. Kernel reiniciou: ao subir, tudo que estava em voo vira `interrupted`. 429 do modelo antes de qualquer ferramenta: volta para a fila com espera crescente.

## 4. Mapa do repositório

```
harness/      serviço (imagem harness)
  api.py        rotas HTTP, SSE, validação do contrato
  kernel.py     montagem e ciclo de vida dos componentes
  scheduler.py  fila Redis Streams, justiça, locks, expiração, reenfileiramento
  dispatcher.py execução da run, timeouts, 429, finalização
  workers.py    registro de workers, saúde, afinidade (rendezvous), slots
  profiles.py   perfil por empresa/template/usuário, materialização de skills
  translate.py  eventos do Hermes -> eventos do produto
  approvals.py  confirmação autenticada (cartão), só com confirm_actions
  runctx.py     estado da run em memória (credenciais, manifesto, contadores)
  security.py   tokens (serviço, run_token, broker) e redação de segredos
  store.py      estado no Redis (runs, idempotência, eventos, locks, afinidade)
  sweep.py      promotor: varre pending/ e envia propostas ao backend
  skillcheck.py validação de pacote de skills e de skill de usuário
  recovery.py   runs órfãs viram interrupted no boot
  backend.py    cliente das rotas /internal/* do agent-platform
  config.py     Settings (tudo por variável de ambiente)
broker/       ferramentas rt_*: service.py (autorização), data.py/sqlio.py (SQL/Mongo),
              pix.py, email_tool.py, http_tool.py, env.py/rm.py (ambiente), schedule.py,
              executor.py, artifacts.py, misc.py, catalog.py (grupos de ferramentas)
plugin/       plugin rt-broker (stubs das ferramentas dentro do worker) e config base
profiles/     profile_config.py (gera config.yaml, .env e plugin do perfil) e templates/base.yaml
skills/system/  skills de sistema (por grupo: core, pix, dados, email, docs, api, analise, rotinas, ambiente)
contracts/    JSON Schemas versionados (v1) do contrato Backend -> kernel
docker/       harness.Dockerfile (alvos dev e prod) e hermes.Dockerfile (imagem rt-hermes)
dev/          pilhas Docker de teste: compose.harness.yml, compose.full.yml, compose.real.yml, smoke.py
devtools/     Hermes falso, backend falso e externos falsos (só teste; alvo dev do Dockerfile)
tests/        suítes offline (pytest)
tests_real/   E2E contra o Hermes real (precisa de dev/compose.real.yml em pé)
scripts/      verify.sh (gate local)
docs/         adr/, runbooks/, qa/, provenance.md, este onboarding e o guia do administrador
runtime/, executor/   reservados (README apenas); o código real está no repo runtime-manager
```

## 5. Rodar e testar

### 5.1 Testes offline (o dia a dia)

```bash
pip install -r requirements-dev.txt
python -m pytest -q        # 100% offline: fakeredis + Hermes falso; sem Docker
ruff check .               # o CI roda os dois
```

As suítes seguem a matriz da spec 04 §14.2:

| Arquivo | Família | O que prova |
|---|---|---|
| `tests/test_contracts.py` | C | contrato de entrada/saída, idempotência, SSE, interrupção, timeouts, uso |
| `tests/test_broker.py` | B | credenciais só em memória, autorização do broker, Pix, e-mail, SQL, `rt_http` |
| `tests/test_queue_pool.py` | P | fila, justiça, worker morto, Redis reiniciado, recuperação |
| `tests/test_skills.py` | S | pacote, camadas, perfil por usuário, promotor |
| `tests/test_env_tools.py`, `test_profile_config.py`, `test_health.py` | | ferramentas de ambiente, gerador de perfil, saúde |

Testes que precisam do Docker CLI levam o marcador `docker`. Cada teste tem timeout de 60 s (`pyproject.toml`).

### 5.2 Gate local: `scripts/verify.sh`

```bash
bash scripts/verify.sh     # precisa de Docker
```

Constrói a **mesma imagem de produção** (`docker/harness.Dockerfile`), sobe, espera `/health` e `/version`, e constrói também a imagem `rt-hermes` (`docker/hermes.Dockerfile`). Falha de verdade (sem `|| true`). É obrigatório antes de qualquer release e o CI o repete. Em Windows, rode pelo Git Bash ou WSL.

### 5.3 Ambiente offline completo (`dev/`)

Só desenvolvimento e teste. Produção vive no repo `vps_rt_infra`.

| Arquivo | O que sobe |
|---|---|
| `dev/compose.harness.yml` | Redis (AOF) + kernel + 2 Hermes **falsos** + backend **falso**. É o padrão para desenvolver. |
| `dev/compose.real.yml` | Kernel + Hermes **real** (`rt-hermes:dev`) + LLM falso determinístico + backend falso. Pré-requisito: `docker build -f docker/hermes.Dockerfile -t rt-hermes:dev .` |
| `dev/compose.full.yml` | Variante com o Hermes real (nome de projeto `harness-full`). |
| `compose.dev.yml` (raiz) | Só o kernel, para olhar a API. |

```bash
docker compose -f dev/compose.harness.yml up --build
python dev/smoke.py "ola"            # envia uma run e imprime o SSE
curl localhost:8080/health

docker compose -f dev/compose.real.yml up -d --build
python -m pytest tests_real -q       # E2E com o Hermes real
```

O LLM falso (`dev/real/fake_llm.py`) é determinístico: responde `CALL:<ferramenta>|<json>` executando a ferramenta, o que permite testar o broker de ponta a ponta sem gastar modelo. Nenhum dado de cliente nem segredo real entra nesses ambientes (os tokens `dev-*` são de teste).

## 6. Onde ficam os contratos

- **Backend para kernel:** `contracts/v1/*.schema.json` (JSON Schema 2020-12, `contract_version: 1`). Mudança incompatível vira `v2/`. O backend copia esses arquivos para os testes dele. `tests/test_contracts.py` valida o que a própria API emite. Eventos desconhecidos devem ser ignorados pelo consumidor.
- **Plugin para broker:** `contracts/v1/broker_call.schema.json` (`{session_id, tool, args, meta}`, token do pool no cabeçalho `Authorization`).
- **Skills:** formato do pacote e regras em `harness/skillcheck.py` (`validate_package`, `validate_user_skill`), exposto em `POST /v1/skills/validate`.
- **Manifesto:** o contrato de entrada fixa `tools`, `credential_grants`, `environment` e `limits` (`confirm_actions`, `write_tables`, `http_allowed_hosts`, `pix_max_amount`, `max_recipients`, `max_tool_calls_per_turn`, `external_approval`...). O gerador de perfil é `profiles/profile_config.py`.
- **Regras de produto e decisões:** specs 04 e 10 (repositório de specs `new_ragente/specs`), `docs/adr/`.

Armadilhas conhecidas nos contratos (confirmadas no código):

- O catálogo de ferramentas aparece em **três lugares** que não coincidem: `DEFAULT_TOOL_CATALOG` em `harness/skillcheck.py` (usado por `/v1/skills/validate` quando o chamador não passa `available_tools`), o catálogo do backend (`app/tool_catalog.py`, o que a tela usa) e as ferramentas do broker (`broker/service.py` e `plugin/rt_broker/__init__.py`). Exemplo: o backend aceita `rt_env_manage` e `rt_job_schedule` (grupos que `broker/catalog.py` expande), que não estão no catálogo padrão do `skillcheck`. Sempre passe `available_tools` ao validar. Ferramentas como `rt_google_calendar`, `rt_google_sheets`, `rt_ms_calendar`, `rt_image_generate` e `rt_template_plan` estão nos catálogos mas **não têm implementação** no broker.
- A varredura de segredos do `skillcheck` recusa o marcador `{{secret:NOME}}` (a palavra `secret`, dois-pontos e 8 ou mais caracteres). Por isso as skills citam o segredo pelo nome, sem o marcador.
- O backend normaliza o manifesto e hoje preserva só `max_turns`, `confirm_actions`, `write_tables` e `http_allowed_hosts` nos limites; o resto que o kernel entende é descartado antes de chegar.
- `allowed_scopes` (restringir um template a certos escopos) **não está implementado** em nenhum dos dois lados.

## 7. Decisões de segurança que você não pode quebrar

1. **Segredo nunca em prompt, skill, perfil, log ou evento.** As credenciais da empresa chegam no `POST /v1/runs`, ficam só em memória (`RunContext`) e são apagadas ao fim da run (inclusive em erro e cancelamento). Nunca vão para o Redis (o corpo guardado é `strip_secrets`), nem para disco, nem para o Hermes. O agente vê apenas um catálogo por **rótulo**. Todo texto que sai do broker, de eventos e de aprovações passa pelo `Redactor`, que troca os valores conhecidos por `[REDACTED]`. A auditoria de ferramentas (`harness.audit`) registra quem, o quê e o resultado, **sem valores**. Não logue o corpo de requisições.
2. **Texto de skill nunca concede permissão.** Ferramentas, credenciais, ambiente e limites vêm do manifesto e da política do backend. Skills de usuário não podem declarar requisitos de ferramenta (`requires_tools`, `prerequisites`...), nem shell inline, nem repetir nome de skill de sistema ou da empresa (prefixo obrigatório `u-`).
3. **O broker autoriza de verdade, não o plugin.** O token do plugin só prova que a chamada veio de um worker nosso. A autorização é casar o `session_id` com uma run ativa, o worker dono dessa run, e a ferramenta no manifesto. Sem run ativa: 401. Sessão de outro worker: 403. Ferramenta fora do manifesto: 403.
4. **Workspace separado.** O worker Hermes não tem terminal, arquivos nem código local (conjuntos `terminal`, `file`, `code_execution`, `browser` etc. são proibidos em `profiles/profile_config.py`). Terminal e arquivos só existem quando a empresa tem **ambiente** (servidor próprio, via SSH, gerenciado pelo `runtime-manager`). Sem ambiente, o backend de terminal aponta para um host sentinela (`ambiente-desligado.invalid`) que nunca resolve, para falhar fechado. Código arbitrário sem ambiente roda no **executor efêmero**, sem rede. O ambiente de uma empresa não alcança LiteLLM, bancos nem outras empresas.
5. **Isolamento entre empresas e usuários.** Perfil por (empresa, template, usuário), com `skills.external_dirs` próprios; uma empresa só tem um worker escritor dos `state.db` dela (afinidade).
6. **Dinheiro e escrita.** Pix com `Decimal` (no máximo 2 casas, nunca arredonda), teto, idempotência por (empresa, chat, referência, valor) e entrega só após aviso oficial do provedor. SQL de escrita: uma instrução, `WHERE` obrigatório em alterar e apagar, lista de tabelas permitidas. `rt_http`: lista de hosts, bloqueio de IP interno e de metadata, redirecionamentos desligados, `{{secret:NOME}}` resolvido só no broker.
7. **Confirmação autenticada é opcional por template.** Padrão: o agente age direto. Com `confirm_actions`, a ação espera um clique autenticado do dono da conversa; canal externo sem aprovador humano e rotina agendada são negados (exceto e-mail em rotina).
8. **Sem GCP.** Nada de Cloud Run, GCS, BigQuery ou `*.googleapis.com` de infraestrutura entra no caminho do serviço; a infraestrutura é a VPS (Docker, Traefik, MinIO/S3, Redis, Postgres). Os testes de aceite da spec 10 verificam que nenhuma requisição vai a `run.app` nem a `*.googleapis.com`. O backup usa um Google Drive pessoal do dono (restic + rclone) e isso é permitido; é armazenamento do dono, não serviço de nuvem do produto. As integrações Google/Microsoft do cliente são OAuth do produto (ainda **não implementadas** no broker).
9. **Este repo é público e nunca faz deploy.** Nada de segredo, `.env`, dado de cliente nem IP de produção entra aqui (`gitleaks` roda em todo PR). Release publica imagem por digest e manifesto; só o `vps_rt_infra` implanta (ADR-002).
10. **Vocabulário ao cliente.** Texto de produto (erros, rótulos, respostas de API) não usa "kernel", "harness", "runtime" nem "Hermes".

## 8. Convenções de trabalho

- Commits em português, Conventional Commits (`feat:`, `fix:`, `docs:`...), sem co-autoria e sem citar ferramentas de IA.
- Toda mudança de comportamento vem com teste nas famílias C, B, P ou S. Mudança de contrato: novo arquivo versionado e teste de contrato.
- `ruff check .` e `python -m pytest -q` precisam passar; antes de release, `scripts/verify.sh`.
- Mudança de infraestrutura (compose, variáveis, rede) vai para o `vps_rt_infra`, não para este repo.

## 9. Lacunas conhecidas (hoje)

- Reciclagem de worker a cada N runs (`recycle_after_runs`): não implementada.
- Fencing de worker no kernel: apenas registra em log (`NoopFencer`); o fencing real é do runtime-manager e não está ligado ao kernel.
- Bloqueio por empresa dentro do kernel: não implementado (usar o kill switch do runtime-manager).
- Restrição de um agente por escopo (`allowed_scopes`): não implementada.
- `poc/contract_battery.py`, citado no `docker/hermes.Dockerfile`: o arquivo não existe; a bateria real hoje é `tests_real/`.
- Ferramentas `rt_google_*`, `rt_ms_*`, `rt_image_generate`, `rt_template_plan`: listadas nos catálogos, sem implementação no broker.
- `docs/qa/` e `docs/provenance.md` ainda são esqueletos.
