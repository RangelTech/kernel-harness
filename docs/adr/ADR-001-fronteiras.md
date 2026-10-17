# ADR-001 — Fronteiras do kernel-harness

**Status:** aceito (17/10/2026).
- `kernel-harness` **substitui o `kernel-llm`** (LangGraph com supervisor + especialistas e ferramentas MCP).
- O `agent-platform` (backend + frontend do RAgent) **continua** e fala com este kernel por um contrato versionado (`contracts/`).
- O **Hermes Agent** (NousResearch, MIT) e projeto externo: usado por imagem fixada por digest, sem fork.
- Decisoes de runtime ja medidas em POC: um container Hermes por tenant, volume proprio, rede interna por tenant, ferramentas por perfil, backup do `state.db` por `sqlite3.backup`, `/compress` enviado pelo kernel.
