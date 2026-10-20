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

# Sem servicos por perfil: o upstream cria um s6-supervise + s6-log por perfil no boot (02-reconcile) e
# sobe um dashboard que o produto nao usa. Remove os dois; o boot deixa de ser proporcional ao n de perfis.
RUN rm -f /etc/cont-init.d/02-reconcile-profiles /opt/hermes/docker/cont-init.d/02-reconcile-profiles \
 && rm -f /etc/s6-overlay/s6-rc.d/user/contents.d/dashboard

# Padroes do modo headless (a chave API_SERVER_KEY e SEMPRE injetada em runtime, nunca na imagem).
ENV API_SERVER_ENABLED=true \
    API_SERVER_HOST=0.0.0.0 \
    API_SERVER_PORT=8642 \
    RT_BROKER_TIMEOUT_S=540 \
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

CMD ["/opt/hermes/.venv/bin/python", "-m", "rt_worker"]
