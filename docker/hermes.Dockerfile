# rt-hermes: imagem derivada do Hermes Agent (NousResearch) para o kernel-harness (spec 04).
# Worker EFEMERO (K-kernel-arquitetura.md): em vez do `gateway run` multiplexado (um processo que serve
# todos os perfis e os percorre parado), o container roda um front leve + um zigoto quente (rt_worker/).
# Cada run vira um filho `fork()` com HERMES_HOME montado so para ela, que morre no fim. Sem s6 por
# perfil, sem gateway em espera, sem TTL. NAO altera o codigo do Hermes (usa o API server dele).
# Upgrade = trocar o digest + rodar tests/ e o QA de carga.
# Build (a partir da raiz do repo):  docker build -f docker/hermes.Dockerfile -t rt-hermes:dev .
ARG HERMES_DIGEST=sha256:36099926da316742423544e055d4734777105ffe6341873af5459b957d0c2919
FROM nousresearch/hermes-agent@${HERMES_DIGEST}
ARG HERMES_DIGEST

# Plugin e config base ficam FORA de /opt/data (VOLUME): sobrevivem a volume vazio/bind-mount.
# O gerador de perfis cria o symlink  <perfil>/plugins/rt-broker -> /opt/rt/plugins/rt-broker
# e liga o plugin em plugins.enabled (o Hermes carrega plugins por perfil e so os habilitados).
COPY plugin/rt_broker /opt/rt/plugins/rt-broker
COPY skills/system /skills/system
COPY plugin/image/config.base.yaml /opt/rt/config.base.yaml
COPY --chmod=0755 plugin/image/00-rt-setup /etc/cont-init.d/00-rt-setup
COPY rt_worker /opt/rt/rt_worker

# Bytecode pre-compilado: a imagem base traz o fonte do Hermes sem __pycache__ e roda com
# PYTHONDONTWRITEBYTECODE=1 em rootfs somente leitura, entao TODA importacao compilava do fonte (zigoto
# aquecia em 35-75 s sob carga e cada filho recompilava ~160 arquivos de plugin: ~0,5 s de CPU por run).
# Os .pyc ficam ao lado do fonte e so sao lidos em runtime. `|| true`: exemplos com sintaxe antiga.
RUN /opt/hermes/.venv/bin/python -m compileall -q -j 0 \
      -x "(python-3\.14|node_modules|\.venv|web_dist|ui-tui|/tests?/|/tools/node-|/docs?/)" \
      /opt/hermes/agent /opt/hermes/gateway /opt/hermes/hermes_cli /opt/hermes/plugins \
      /opt/hermes/tools /opt/hermes/cron /opt/hermes/acp_adapter /opt/hermes/hermes_platform \
      /opt/rt >/dev/null 2>&1 \
    && /opt/hermes/.venv/bin/python -m compileall -q -l /opt/hermes >/dev/null 2>&1 || true

# Padroes do modo headless (a chave API_SERVER_KEY e SEMPRE injetada em runtime, nunca na imagem).
ENV API_SERVER_ENABLED=true \
    API_SERVER_HOST=0.0.0.0 \
    API_SERVER_PORT=8642 \
    RT_BROKER_TIMEOUT_S=1830 \
    PYTHONPATH=/opt/rt \
    HERMES_PYTHON=/opt/hermes/.venv/bin/python \
    RT_WORKER_SLOTS=16 \
    RT_WORKER_RUN_MAX_S=3600

LABEL org.opencontainers.image.description="rt-hermes: Hermes Agent headless + plugin rt-broker (kernel-harness)" \
      org.opencontainers.image.source="https://github.com/RangelTech/kernel-harness" \
      rt.hermes.base_digest="${HERMES_DIGEST}"

EXPOSE 8642
# /health e do front (aberto, sem auth) e responde na hora: nao depende do numero de perfis.
HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=4 \
  CMD ["curl", "-fsS", "-m", "4", "-o", "/dev/null", "http://127.0.0.1:8642/health"]

# Sem s6-overlay: o processo do worker e o PID 1 (init: true no compose reapa orfaos). Se o front cair o
# container sai e o Docker o recria; o zigoto e reiniciado pelo front. Roda como o usuario hermes (10000),
# dono dos volumes de perfis, entao nao ha bootstrap de root nem servico por perfil.
USER 10000:10000
ENV HOME=/opt/data
ENTRYPOINT ["/opt/hermes/.venv/bin/python", "-m", "rt_worker"]
CMD []
