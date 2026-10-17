# ADR-002 — Entrega por artefato

**Status:** aceito (17/10/2026).
- Este repo **nunca** conhece a VPS nem guarda credenciais de deploy.
- `release.yml` publica `ghcr.io/rangeltech/kernel-harness/harness@sha256:<digest>` e anexa `release-manifest.json` na GitHub Release.
- `RangelTech/vps_rt_infra` (unico com segredos da VPS) le o manifesto, valida (remetente, digest, schema), fixa o digest em `releases/kernel-harness.lock` e faz o deploy com healthcheck real e rollback automatico.
- Gate local obrigatorio: `scripts/verify.sh`. Sem staging (decisao do dono): teste local + deploy por digest + rollback.
