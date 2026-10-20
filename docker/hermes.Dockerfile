# rt-hermes: imagem derivada do Hermes Agent (NousResearch) para o kernel-harness (spec 04).
# ACRESCENTA: plugin rt-broker, config base, marcador "sem skills embutidas" e healthcheck.
# Unica alteracao no Hermes: docker/patches/hermes_idle_cpu.py (pollers por perfil ganham variavel de
# ambiente; o padrao sem variavel e o do upstream). Upgrade = trocar o digest + revisar o patch (o build
# falha se uma ancora mudar) + rodar poc/contract_battery.py.
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
COPY docker/patches/hermes_idle_cpu.py /tmp/hermes_idle_cpu.py
RUN /opt/hermes/.venv/bin/python /tmp/hermes_idle_cpu.py /opt/hermes && rm -f /tmp/hermes_idle_cpu.py

# Padroes do modo headless (a chave API_SERVER_KEY e SEMPRE injetada em runtime, nunca na imagem).
ENV API_SERVER_ENABLED=true \
    API_SERVER_HOST=0.0.0.0 \
    API_SERVER_PORT=8642 \
    RT_BROKER_TIMEOUT_S=540 \
    RT_HERMES_CRON=0 \
    RT_HERMES_MCP_RECONCILE=0 \
    RT_HERMES_HANDOFF_POLL_S=30 \
    RT_HERMES_LOOP_POLL_S=120 \
    RT_HERMES_ORPHAN_SWEEP_S=900 \
    RT_HERMES_MEM_TRIM_COOLDOWN_S=900

LABEL org.opencontainers.image.description="rt-hermes: Hermes Agent headless + plugin rt-broker (kernel-harness)" \
      org.opencontainers.image.source="https://github.com/RangelTech/kernel-harness" \
      rt.hermes.base_digest="${HERMES_DIGEST}"

EXPOSE 8642
# O Hermes nao traz HEALTHCHECK; /health e aberto (sem auth). curl (~10 ms de CPU) no lugar de
# python+urllib (~150 ms por execucao); intervalo 30 s e timeout 10 s porque o /health e servido pelo mesmo
# loop que roda os turnos e atrasa sob carga (nao e sinal de worker morto); o boot reconcilia ~200 perfis
# (5 a 16 min), por isso start-period de 600 s.
HEALTHCHECK --interval=30s --timeout=10s --start-period=600s --retries=5 \
  CMD ["curl", "-fsS", "-m", "8", "-o", "/dev/null", "http://127.0.0.1:8642/health"]

CMD ["gateway", "run"]
