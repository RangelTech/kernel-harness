# Runbook de operação: kernel de agentes por skills

Para quem opera a VPS. Cobre o serviço `kernel-harness` (API, fila, broker) e os dois workers Hermes (`rt-hermes-w1`, `rt-hermes-w2`). Só aparecem comandos e caminhos que existem nos repositórios `kernel-harness`, `vps_rt_infra` e `runtime-manager`. Onde algo não existe, está escrito **não implementado**.

> Este repositório nunca faz deploy e não conhece a VPS (ADR-002). Os comandos abaixo rodam **na VPS** (por SSH, com usuário de deploy e `sudo`) ou no GitHub (`gh`). Nada neste documento foi executado ao escrevê-lo.

## 0. Mapa rápido

| O quê | Onde |
|---|---|
| Stack | `/opt/platform/stacks/kernel-harness/compose.yml` (projeto Compose `kernel-harness`) |
| Imagens em uso | `/opt/platform/stacks/kernel-harness/.release.env` (`KERNEL_HARNESS_IMAGE`, `RT_HERMES_IMAGE`) |
| Segredos (0600) | `/opt/platform/secrets/kernel-harness.env` (kernel), `/opt/platform/secrets/rt-hermes.env` (workers) |
| Estado do último deploy | `/opt/platform/state/<servico>.current` e `/opt/platform/state/<servico>.last_deploy.json` (`<servico>` = `kernel-harness` ou `rt-hermes`) |
| Locks de release | `releases/kernel-harness.lock`, `releases/rt-hermes.lock` no repo `vps_rt_infra` |
| Containers | `kernel-harness`, `rt-hermes-w1`, `rt-hermes-w2`, `redis`, `runtime-manager`, `rt-docker-proxy` |
| Volumes de dados | `kernel-harness_profiles`, `kernel-harness_users`, `kernel-harness_tenant_skills` (mais `kernel-harness_home1` e `kernel-harness_home2`, um por worker) |
| Fila e estado | Redis central, container `redis`, usuário ACL `kernelharness` (a stack indica db 3) |
| Saúde do kernel | `GET /health` (porta 8080, interna), `GET /v1/stats` (token de serviço) |

Pontos de atenção que valem para todo o runbook:

- O kernel **não publica porta** no host. Para chamar a API dele, use `docker exec kernel-harness ...` (veja 3.1).
- O pool de produção tem **2 workers de 4 slots** (`HARNESS_WORKERS` no compose), ou seja, 8 runs simultâneas no máximo. Os workers estão em redes internas sem internet; falam só com o kernel (broker) e o LiteLLM.
- O `rt-healer` (timer systemd) recoloca containers caídos. Para manutenção que derruba containers de propósito, crie o arquivo `/opt/platform/.maintenance` (o healer o respeita) e apague no fim.
- No repositório `vps_rt_infra`, o Infisical também usa Redis db 3 (`stacks/platform/compose.yml`, `REDIS_URL ...@redis:6379/3`). **Nunca rode `FLUSHDB`/`FLUSHALL`** no Redis central. Confira o db efetivo do kernel no `REDIS_URL` de `kernel-harness.env`.

## 1. Deploy por digest (via `vps_rt_infra`)

Fluxo: tag no `kernel-harness` publica os artefatos, o `vps_rt_infra` grava o lock com sha256 e image id, e o deploy valida tudo antes de trocar.

1. **Publicar a release.** No repo `kernel-harness`, `scripts/verify.sh` precisa passar (o CI repete). Depois:
   ```bash
   git tag vX.Y.Z && git push origin vX.Y.Z      # dispara .github/workflows/release.yml
   ```
   A release anexa `kernel-harness.image.tar.gz`, `rt-hermes.image.tar.gz` e os manifestos `release-manifest.kernel-harness.json` e `release-manifest.rt-hermes.json`.
2. **Gravar o lock.** O workflow `Release intake` do `vps_rt_infra` roda a cada 20 minutos (ou `gh workflow run release-intake.yml`). Ele valida o manifesto (repo, schema, sha256, image id, asset) e faz commit de `releases/kernel-harness.lock` e `releases/rt-hermes.lock` (campos `artifact_sha256`, `image_id`, `previous_version`, `previous_image_id`). As duas fontes estão na camada B: **o deploy é manual**.
3. **Escolher o momento.** Troca de imagem derruba as runs em voo (elas terminam como `interrupted`). Veja runs ativas (3.1) e prefira horário calmo.
4. **Disparar o deploy** (um serviço por vez; cada serviço tem seu próprio lock de concorrência no workflow):
   ```bash
   gh workflow run deploy.yml -f service=kernel-harness -R RangelTech/vps_rt_infra
   gh workflow run deploy.yml -f service=rt-hermes      -R RangelTech/vps_rt_infra
   ```
   O workflow envia o lock, `deploy.sh`, `lib.sh` e a pasta da stack por rsync (sem `--delete`) e executa `scripts/deploy.sh <servico>` na VPS.
5. **O que o `deploy.sh` faz.** Baixa o artefato, confere o sha256 e o image id (`image_id` do lock); confere se as variáveis de `env_required` existem em `/opt/platform/secrets/<servico>.env` (valor nunca impresso); `docker load`; tag local imutável `rt/<servico>:<12 primeiros hex do image id>`; `docker compose up -d --no-build --no-deps --pull never` só dos serviços do lock; espera o HEALTHCHECK de cada container (kernel 90 s, workers 120 s). Container sem HEALTHCHECK é reprovado.
6. **Verificar.**
   ```bash
   cat /opt/platform/state/kernel-harness.last_deploy.json
   docker ps --filter name=kernel-harness --filter name=rt-hermes --format '{{.Names}} {{.Status}}'
   ```
   Depois rode a verificação funcional de 3.1 (`/v1/stats`, workers saudáveis).

Variáveis exigidas hoje pelo lock do kernel: `HARNESS_SERVICE_TOKENS`, `HARNESS_RUN_TOKEN_SECRET`, `HARNESS_BROKER_TOKENS`, `BACKEND_INTERNAL_TOKEN`, `HERMES_MASTER_SECRET`, `LLM_WORKER_KEY`. Dos workers: `RT_BROKER_TOKEN`. Se faltar alguma, o deploy aborta antes de trocar qualquer coisa.

Ordem sugerida em release que muda as duas imagens: `rt-hermes` primeiro e `kernel-harness` depois, porque o kernel regenera os perfis quando o hash do template muda e os workers precisam do plugin novo. Os dois workers sobem juntos (um serviço de lock com `compose_service=hermes-w1,hermes-w2`); **não há onda**.

## 2. Rollback

### 2.1 Automático (o caso comum)

Se o healthcheck falhar após o `up`, o `deploy.sh` mostra os últimos 30 logs, **sobe a tag anterior** (`/opt/platform/state/<servico>.current`) e repete a verificação. O job do GitHub fica vermelho. Se não houver versão anterior, ele só para os containers reprovados.

### 2.2 Manual (a versão nova passou no healthcheck, mas está errada)

O `deploy.sh` guarda as duas imagens mais recentes de cada serviço. Os campos `previous_*` do lock são só informativos: o `deploy.sh` **não** os lê.

```bash
# na VPS
docker images 'rt/kernel-harness'            # tags: rt/kernel-harness:<12 hex>
cd /opt/platform/stacks/kernel-harness
# edite .release.env e ponha a tag anterior em KERNEL_HARNESS_IMAGE (ou RT_HERMES_IMAGE para os workers)
sudo docker compose -p kernel-harness -f compose.yml --env-file .release.env \
     up -d --no-build --no-deps --pull never harness            # workers: hermes-w1 hermes-w2
echo rt/kernel-harness:<tag-anterior> | sudo tee /opt/platform/state/kernel-harness.current
```

Atenção: o `Release intake` lê sempre a **última release** do GitHub. Reverter só o commit do lock é desfeito pelo intake na próxima rodada. O caminho definitivo é corrigir no `kernel-harness` e publicar uma nova tag. Enquanto isso, **ninguém deve rodar `deploy.yml`** para esse serviço, porque ele reinstalaria a versão ruim.

Não faça `docker compose down -v` nem apague os volumes `kernel-harness_*`: eles guardam sessões e perfis (seção 7).

## 3. Operação do dia a dia

### 3.1 Saúde e estatísticas

O kernel escuta em `8080` dentro do container. O token de serviço fica no ambiente do container; o comando abaixo o lê sem imprimi-lo.

```bash
# vivo?
docker exec kernel-harness python -c "import urllib.request as u;print(u.urlopen('http://127.0.0.1:8080/health').read().decode())"
# fila, workers, runs ativas
docker exec kernel-harness python -c "import os,urllib.request as u;t=os.environ['HARNESS_SERVICE_TOKENS'].split(',')[0];r=u.Request('http://127.0.0.1:8080/v1/stats',headers={'Authorization':'Bearer '+t});print(u.urlopen(r).read().decode())"
```

`/health` informa `ready` e `workers_healthy`. `/v1/stats` traz `scheduler` (fila), `workers` (cada um com `healthy`, `in_use`, `slots`, `runs_total`, `reason`), `active_runs`, `dispatcher` e `broker` (chamadas, negadas, erros). Se `ready` for `false`, o Redis está inacessível: o processo fica de pé, responde 503 em `/v1/runs` e tenta reconectar sozinho a cada 2 s.

Logs: `docker logs --tail 100 kernel-harness`. A auditoria de ferramentas sai no logger `harness.audit` (JSON por chamada, **sem valores de credencial**: run, empresa, ferramenta, tipo de credencial, resultado, tempo).

### 3.2 Reinício

| Componente | Comando | Efeito nas runs |
|---|---|---|
| Kernel | `docker restart kernel-harness` | Ao subir, toda run não terminada vira `interrupted` com o código `kernel_restarted` (as credenciais viviam só em memória, a run não pode continuar). O usuário vê "A execução foi interrompida antes de terminar. Envie a mensagem novamente." Nunca vira sucesso. |
| Um worker | `docker restart rt-hermes-w1` | Runs dele viram `interrupted`. O kernel marca o worker fora e os tenants dele migram (3.3). Runs ainda na fila são reatribuídas. |
| Redis | reinício do container `redis` (AOF ligado) | O kernel reconecta; runs em voo ficam `interrupted`. |

Para parar de propósito (manutenção), crie `/opt/platform/.maintenance` antes e apague depois.

### 3.3 Pool de workers e afinidade

- **Definição do pool:** variável `HARNESS_WORKERS` (JSON com `id`, `url`, `slots`) no `compose.yml` do serviço `harness`. Cada worker tem o seu `HERMES_HOME` (volume `home1`, `home2`); os perfis ficam no volume compartilhado `profiles`.
- **Afinidade:** cada empresa fica presa a **um** worker (hash de rendezvous entre os saudáveis), para existir um único escritor dos `state.db` dos perfis dela. O vínculo fica no hash Redis `affinity` (campo = id da empresa, valor = id do worker). A empresa só troca de worker se o dela cair.
- **Fila justa:** teto de `HARNESS_TENANT_CONCURRENCY` runs simultâneas por empresa (padrão 2), expiração na fila em 60 s (a run termina com `queue_expired`), no máximo 1.000 runs na fila no total e 50 por empresa (acima, a API responde 429 `backpressure` com `Retry-After: 5`). Expiração e tetos de fila são valores fixos no código (`harness/config.py`), **sem variável de ambiente**; já `HARNESS_TENANT_CONCURRENCY` é configurável. Uma run por chat de cada vez (lock `lock:chat:<empresa>:<chat>` com TTL de 15 s, renovado).
- **Worker caiu:** o kernel testa `GET /health` de cada worker a cada 5 s. Worker fora: runs que ainda não começaram são reatribuídas; as em voo viram `interrupted`. A empresa passa para outro worker.
- **Fencing real: não implementado no kernel.** No `Kernel` o `Fencer` padrão é o `NoopFencer`, que só registra "fencing solicitado" no log. Por isso, ao tirar um worker de operação, **pare o container** (`docker stop rt-hermes-w1`) em vez de apenas deixá-lo "doente", para não haver dois escritores no mesmo `state.db`.
- **Voltar uma empresa ao worker original:** depois que o worker volta, as empresas já migradas **não voltam sozinhas** (o vínculo é fixo). Se precisar, apague o vínculo no Redis (`HDEL affinity <id-da-empresa>`) com o worker de destino saudável e chame `POST /v1/tenants/<id>/ensure` (reatribui).
- **Reciclagem de worker a cada N runs (`recycle_after_runs`): não implementado** no kernel (a spec prevê, o código não tem). Reinício manual é o caminho.
- **Aumentar o pool:** acrescentar um serviço `hermes-w3` no `compose.yml` (mesmo padrão dos atuais), um volume `home3`, o diretório em `HOMEDIRS` do `init-home`, e uma entrada em `HARNESS_WORKERS`. Rode `docker compose --profile init run --rm init-home` (a stack documenta isso para a primeira subida; recria o `HERMES_HOME` só se a configuração base mudar). Mudança de compose passa pelo repo `vps_rt_infra`, nunca por edição direta na VPS.

### 3.4 Fila Redis

Redis Streams e chaves usadas pelo kernel (todas sem credenciais; o corpo guardado da run **não inclui** `credentials` nem `run_token`):

| Chave | O que é | Validade |
|---|---|---|
| `runs:queue` (grupo `dispatch`) | fila de runs | até processar |
| `run:<run_id>` | estado da run (`status`, `terminal`, `error_code`...) | 24 h |
| `runbody:<run_id>` | pedido sem segredos, para a fila | 24 h |
| `events:<run_id>` | eventos SSE (reconexão por `Last-Event-ID`) | 10 min |
| `idem:<empresa>:<chave>` | idempotência | 24 h |
| `lock:chat:<empresa>:<chat>` | uma run por chat | 15 s, renovado |
| `affinity` | empresa para worker | permanente |
| `approval:<id>` | confirmações pendentes | 1 h |
| `usage_prev:<sessão>` | consumo anterior | 30 dias |

Acesso de leitura (digite a senha do usuário `kernelharness` quando pedir; ela está no cofre, nunca em linha de comando):

```bash
docker exec -it redis redis-cli --user kernelharness --askpass -n 3
> XLEN runs:queue
> XPENDING runs:queue dispatch
> HGETALL run:<run_id>
> HGETALL affinity
```

O Redis central roda com `appendonly yes`, `maxmemory 2gb` e `noeviction`. Se o Redis encher, a gravação falha em vez de descartar dados: monitore o uso.

### 3.5 429 do modelo

O que o kernel já faz sozinho:

1. **429/503/529 do provedor de modelo antes de qualquer ação** (nenhuma ferramenta chamada na run): a run volta para a fila na ordem de chegada, com o passo "Aguardando o modelo liberar". Espera crescente com jitter: `HARNESS_MODEL_RETRY_S` (8 s) x (tentativa + 1), no máximo 20 s por espera, até **2 repetições** (3 tentativas). O worker Hermes não repete o 429 por conta própria (`api_max_retries=1`, `auto_recovery_cycles=0`): antes ele dormia o `Retry-After` do gateway (até 10 min) em silêncio e a run morria no silêncio de 120 s com "demorou mais que o esperado".
2. **429 do worker** (limite de concorrência do próprio Hermes): a run volta para a fila em 0,5 s.
3. **Depois que alguma ferramenta já rodou**, o kernel **não** repete (evita cobrança ou e-mail duplicado). A run termina com erro `model_rate_limited` ("O modelo está ocupado ou o limite de uso foi atingido. Tente novamente em instantes.") e **nunca** como sucesso.
3b. Credencial recusada ou gateway fora (401/403/5xx): erro `model_unavailable` ("O modelo de IA está indisponível no momento..."), sem repetir.
4. Fila cheia: a API devolve 429 `backpressure` (`Retry-After: 5`) para o backend.

O que fazer quando o 429 persiste:

1. Confirme a origem: `docker logs --since 15m kernel-harness | grep -i "rate limit"` (o aviso é "modelo em rate limit run=..."). Veja também os logs do LiteLLM (`docker logs --tail 200 litellm`).
2. Veja se o problema é do provedor (cota, chave revogada) no LiteLLM. O alias de modelo dos agentes é `agente-padrao`; a troca de provedor ou fallback é configuração do LiteLLM (stack `litellm`), não do kernel.
3. Reduza a pressão: baixe `HARNESS_TENANT_CONCURRENCY` ou os `slots` em `HARNESS_WORKERS` (via compose no `vps_rt_infra`) e aumente `HARNESS_MODEL_RETRY_S`.
4. Avise o usuário: a mensagem ao cliente já é amigável. Não reenvie em massa (cada reenvio é uma run nova).

### 3.6 Run presa

Sintoma: usuário vê "Na fila" ou a conversa não termina. Antes de mexer, entenda que existem tetos automáticos: silêncio de 120 s e turno de 1800 s (`HARNESS_SILENCE_TIMEOUT_S`, `HARNESS_TURN_TIMEOUT_S`), confirmação de 120 s (`HARNESS_APPROVAL_TIMEOUT_S`; enquanto espera, os tetos pausam) e fila de 60 s (fixo). Espere esses prazos antes de agir.

Diagnóstico:

```bash
# estado da run
docker exec -it redis redis-cli --user kernelharness --askpass -n 3 HGETALL run:<run_id>
# ou pela API (token de serviço; informe o tenant)
docker exec kernel-harness python -c "import os,urllib.request as u;t=os.environ['HARNESS_SERVICE_TOKENS'].split(',')[0];r=u.Request('http://127.0.0.1:8080/v1/runs/<run_id>?tenant_id=<empresa>',headers={'Authorization':'Bearer '+t});print(u.urlopen(r).read().decode())"
```

Do mais leve ao mais pesado:

1. **Cancelar a run** (`POST /v1/runs/<run_id>/cancel` com `{"tenant_id": "<empresa>"}`). Se estiver na fila, é removida (`cancelled`); se estiver rodando, sinaliza o cancelamento (`cancelling`); se já terminou, devolve `already_finished`.
   ```bash
   docker exec kernel-harness python -c "import os,json,urllib.request as u;t=os.environ['HARNESS_SERVICE_TOKENS'].split(',')[0];r=u.Request('http://127.0.0.1:8080/v1/runs/<run_id>/cancel',data=json.dumps({'tenant_id':'<empresa>'}).encode(),method='POST',headers={'Authorization':'Bearer '+t,'Content-Type':'application/json'});print(u.urlopen(r).read().decode())"
   ```
2. **Chat travado por lock.** O lock `lock:chat:...` tem TTL de 15 s e só é renovado enquanto a run existe; some sozinho. Não apague à mão se houver run ativa para o chat.
3. **Reiniciar o kernel** (`docker restart kernel-harness`) é o caminho para run "fantasma" em `running`/`queued` no Redis sem processo real: a recuperação de boot marca todas como `interrupted` (`kernel_restarted`). Custa interromper as runs legítimas em voo; faça fora do pico.
4. **Worker travado** (run em `running` e `workers[].in_use` alto, mas sem eventos): `docker restart rt-hermes-wN`. As runs desse worker viram `interrupted`.

Não existe comando de "limpeza em massa" no kernel; **não apague chaves `run:*` ou `runs:queue` à mão**.

## 4. Kill switch por empresa (runtime-manager)

O kernel **não** tem bloqueio por empresa próprio (não implementado). O kill switch é do `runtime-manager` (RM) e age sobre o **ambiente** da empresa e sobre a **chave virtual do LiteLLM** dela (sem chave, o agente da empresa deixa de falar com o modelo):

`POST /v1/tenants/<tenant-rm>:suspend` com corpo `{"reason": "...", "severity": "high"}` (use `"critical"` para parar o ambiente em vez de pausar):

1. corta as rotas e a rede da empresa (regras de firewall gravadas pelo RM; o host as aplica pela unit `rt-nft.path`/`rt-nft.service`);
2. tira um retrato forense (inspect, estatísticas e as últimas 200 linhas de log) em `RM_FORENSICS_DIR` (`/var/lib/rt/forensics/<empresa>/<data>/snapshot.json`);
3. pausa (ou para) o ambiente e para o runtime da empresa;
4. revoga a chave virtual do LiteLLM.

Retomar: `POST /v1/tenants/<tenant-rm>:resume` com `{"approver": "<outra pessoa>"}`. Exige serviço administrador **e um segundo aprovador diferente de quem suspendeu**. Kill switch global: `POST /v1/fleet:freeze` com `{"reason": "..."}` (para todos os ambientes).

O id da empresa no RM é derivado do id da plataforma: `t` + os 12 primeiros hexadecimais do UUID (sem hífens).

A chamada exige assinatura HMAC (`X-RM-Service`, `X-RM-Iat`, `X-RM-Scope`, `X-RM-Signature`; janela de 30 s) de um serviço listado em `RM_ADMIN_SERVICES`. Para assinar sem copiar segredos, rode dentro do container do RM, usando as variáveis dele:

```bash
docker exec runtime-manager python - <<'PY'
import os, json, urllib.request as u
from rm.auth import sign
keys = dict(p.split(":", 1) for p in os.environ["RM_SERVICE_KEYS"].split(",") if ":" in p)
svc = os.environ["RM_ADMIN_SERVICES"].split(",")[0]
tenant = "t0123456789ab"                  # troque pelo id do RM da empresa
path = f"/v1/tenants/{tenant}:suspend"
body = json.dumps({"reason": "incidente", "severity": "high"}).encode()
h = sign(keys[svc], svc, "*", "POST", path, body)
h.update({"Content-Type": "application/json", "Idempotency-Key": "suspend-" + tenant})
r = u.Request("http://127.0.0.1:8080" + path, data=body, headers=h, method="POST")
print(u.urlopen(r).read().decode())
PY
```

Confirmação no host: `systemctl status rt-nft.path rt-nft.service` e `nft list table inet rt_fw`. Detalhes em `repos/runtime-manager/docs/runbooks/kill-switch.md`. Obs.: o escopo `*` exige serviço administrador; o RM escuta em `8080` dentro do container.

## 5. Backup e restore

### 5.1 O que o backup cobre

O `scripts/backup.sh` (timer `rt-backup`, DAG `backup_vps_diario` no Airflow pessoal) envia para o Google Drive, via restic + rclone, entre outros:

- `/var/lib/docker/volumes/kernel-harness_profiles` (perfis, `state.db`, memórias e pendentes),
- `/var/lib/docker/volumes/kernel-harness_users` (skills por usuário),
- `/var/lib/docker/volumes/kernel-harness_tenant_skills` (pacotes de skills das empresas),
- `runtime-manager_rmdata`, `/srv/rt/tenants` e `/srv/rt/hermes` (ambientes das empresas).

Retenção: 7 diários, 4 semanais, 6 mensais. O script grava `last_ok.json` só quando tudo deu certo e o `healthcheck.sh` exige backup com menos de 26 horas.

**Ressalvas reais:**

- Os volumes são copiados **a quente**, arquivo a arquivo. O script não usa `sqlite3 .backup`, então um `state.db` pode vir inconsistente.
- O workflow `backup-verify` (semanal) prova a restauração dos **dumps de Postgres** (`chatwoot_prod`, `infisical`), não a dos volumes `kernel-harness_*`. A restauração desses volumes **não tem ensaio automatizado**.
- O Redis (fila) não é restaurado: a fila é efêmera por desenho. Runs em voo se perdem e viram `interrupted`.

### 5.2 O que se perde se um volume sumir

| Volume | Consequência | Reconstrução |
|---|---|---|
| `tenant_skills` | agente sem o pacote da empresa | automática: na próxima run o kernel rematerializa o pacote a partir do que o backend envia (arquivos ou URL) |
| `users` | skills aprendidas do usuário ausentes no disco | automática: o kernel regrava a partir das skills ativas enviadas pelo backend (a cópia de verdade fica no Postgres do backend) |
| `profiles` | config e chave do perfil | automática (o kernel regenera quando o hash muda ou o perfil não existe) |
| `profiles` (`state.db`, memórias) | **histórico de sessão perdido** | só pelo backup |

Ou seja, só o histórico guardado pelo Hermes (`state.db`) depende do restore.

### 5.3 Restaurar

Faça em janela de manutenção (`/opt/platform/.maintenance`) e **primeiro para uma pasta temporária**.

```bash
sudo -i
set -a; . /opt/platform/secrets/restic.env; set +a
export RCLONE_CONFIG=/opt/platform/secrets/rclone.conf
restic snapshots --tag daily --host rt-vps                 # escolha o snapshot
mkdir -p /root/restore-kh
restic restore <snapshot> --target /root/restore-kh \
  --include /var/lib/docker/volumes/kernel-harness_profiles
# pare os componentes que escrevem no volume
cd /opt/platform/stacks/kernel-harness
docker compose -p kernel-harness -f compose.yml stop hermes-w1 hermes-w2 harness
# copie de volta (mantendo dono e permissões), depois suba
rsync -a /root/restore-kh/var/lib/docker/volumes/kernel-harness_profiles/_data/ \
         /var/lib/docker/volumes/kernel-harness_profiles/_data/
docker compose -p kernel-harness -f compose.yml --env-file .release.env up -d --no-build --pull never hermes-w1 hermes-w2 harness
```

Repita com `kernel-harness_users` e `kernel-harness_tenant_skills` se necessário. Depois: `/health`, uma conversa de teste e confira que a sessão anterior continua (o histórico vem do `state.db` restaurado). Apague `/root/restore-kh` ao terminar.

Se o `state.db` restaurado estiver corrompido, apague só o arquivo daquele perfil (o resto é regenerado): o chat perde o histórico, mas volta a funcionar.

## 6. Upgrade do Hermes com bateria de contrato

O Hermes Agent (NousResearch) é consumido por imagem fixada **por digest** (`ARG HERMES_DIGEST` no `docker/hermes.Dockerfile`). Nós só acrescentamos o plugin `rt-broker`, a configuração base e o healthcheck; nada do Hermes é alterado. O kernel depende de comportamentos internos dele (formato de `pending/`, eventos de stream, configuração de perfil), por isso todo upgrade passa por esta sequência:

1. Em um branch do `kernel-harness`, troque `HERMES_DIGEST` no `docker/hermes.Dockerfile` pelo digest novo (e atualize a referência `hermes_pinned` em `harness/api.py` se for o caso).
2. Rode a suíte offline: `python -m pytest -q` (Hermes falso; garante que o kernel continua coerente com o contrato).
3. Construa e suba a pilha com o **Hermes real**:
   ```bash
   docker build -f docker/hermes.Dockerfile -t rt-hermes:dev .
   docker compose -f dev/compose.real.yml up -d --build
   python -m pytest tests_real -q
   ```
   `tests_real/test_real_e2e.py` e `tests_real/test_full_stack.py` são a bateria de contrato contra o Hermes real (contrato, skills, isolamento entre empresas e usuários, pool e fila), com LLM falso e determinístico.
4. Só com tudo verde: `scripts/verify.sh`, tag, release e deploy (seção 1), e rode de novo `tests_real` e uma conversa real na VPS.
5. Em caso de problema, o rollback do `rt-hermes` é o da seção 2.

**Lacuna conhecida:** o comentário do `docker/hermes.Dockerfile` e o código de `harness/sweep.py` citam `poc/contract_battery.py` (bateria "H6", formato de `pending/`), mas **esse arquivo não existe no repositório**. A cobertura do formato de `pending/` (aprendizado de skills) com o Hermes real está **pendente** (item A-3 da spec 10: a skill proposta ainda não chega ao kernel com o Hermes real). Trate o upgrade como de risco até isso ser coberto.

O pool de produção **não** usa a atualização em ondas do RM (`/v1/pool/upgrade`): `RM_POOL_SIZE` é `0` e os workers são serviços do Compose. Os dois sobem juntos no deploy.
