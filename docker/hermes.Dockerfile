# rt-hermes: imagem derivada do Hermes Agent (NousResearch) para o kernel-harness (spec 04).
# So ACRESCENTA: plugin rt-broker, config base, marcador "sem skills embutidas" e healthcheck.
# NAO altera o Hermes. Upgrade = trocar o digest + rodar poc/contract_battery.py.
# Build (a partir da raiz do repo):  docker build -f docker/hermes.Dockerfile -t rt-hermes:dev .
ARG HERMES_DIGEST=sha256:36099926da316742423544e055d4734777105ffe6341873af5459b957d0c2919
FROM nousresearch/hermes-agent@${HERMES_DIGEST}
ARG HERMES_DIGEST

# Plugin e config base ficam FORA de /opt/data (VOLUME): sobrevivem a volume vazio/bind-mount.
# O gerador de perfis cria o symlink  <perfil>/plugins/rt-broker -> /opt/rt/plugins/rt-broker
# e liga o plugin em plugins.enabled (o Hermes carrega plugins por perfil e so os habilitados).
COPY plugin/rt_broker /opt/rt/plugins/rt-broker
COPY plugin/image/config.base.yaml /opt/rt/config.base.yaml
COPY --chmod=0755 plugin/image/00-rt-setup /etc/cont-init.d/00-rt-setup

# Padroes do modo headless (a chave API_SERVER_KEY e SEMPRE injetada em runtime, nunca na imagem).
ENV API_SERVER_ENABLED=true \
    API_SERVER_HOST=0.0.0.0 \
    API_SERVER_PORT=8642 \
    RT_BROKER_TIMEOUT_S=30

LABEL org.opencontainers.image.description="rt-hermes: Hermes Agent headless + plugin rt-broker (kernel-harness)" \
      org.opencontainers.image.source="https://github.com/RangelTech/kernel-harness" \
      rt.hermes.base_digest="${HERMES_DIGEST}"

EXPOSE 8642
# O Hermes nao traz HEALTHCHECK; /health e aberto (sem auth).
HEALTHCHECK --interval=15s --timeout=4s --start-period=90s --retries=5 \
  CMD ["/opt/hermes/.venv/bin/python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8642/health', timeout=3).status == 200 else 1)"]

CMD ["gateway", "run"]
