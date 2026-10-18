FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY harness ./harness
COPY broker ./broker
COPY contracts ./contracts
COPY skills ./skills
ARG GIT_COMMIT=unknown
ARG SOURCE_URL=https://github.com/RangelTech/kernel-harness
ENV GIT_COMMIT=${GIT_COMMIT} PORT=8080 SYSTEM_SKILLS_DIR=/srv/skills/system
LABEL org.opencontainers.image.source=${SOURCE_URL} \
      org.opencontainers.image.revision=${GIT_COMMIT} \
      org.opencontainers.image.description="kernel-harness: kernel de IA do RAgent (API, fila Redis, dispatchers, broker)"

# alvo de desenvolvimento/teste: acrescenta o Hermes falso e o backend falso (NUNCA em producao)
FROM base AS dev
COPY devtools ./devtools
RUN useradd -r -u 10001 harness
USER harness
EXPOSE 8080

# alvo de producao (padrao: ultimo estagio)
FROM base AS prod
RUN useradd -r -u 10001 harness
USER harness
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=2).status==200 else 1)"
CMD ["sh", "-c", "uvicorn harness.main:app --host 0.0.0.0 --port ${PORT}"]
