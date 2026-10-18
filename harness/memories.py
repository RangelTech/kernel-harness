"""Extracao de memorias de longo prazo (spec 4.3 `/v1/memories/extract`; logica do kernel-llm:
so mensagens ainda nao lidas, janela de 8, 1500 chars por mensagem, dedupe, falha silenciosa).

A marca d'agua de mensagens lidas e o armazenamento (pgvector) ficam na plataforma; aqui so
sai a lista de fatos candidatos.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata

import httpx

from harness.config import Settings

log = logging.getLogger("harness.memories")

PROMPT = (
    "Extraia da conversa abaixo fatos duradouros e uteis sobre o USUARIO (preferencias, dados "
    "profissionais, decisoes recorrentes). Ignore saudacoes, pedidos pontuais, segredos, senhas "
    "e dados de terceiros. Responda SOMENTE com um array JSON de strings curtas, em portugues. "
    "Se nao houver nada relevante, responda []."
)
MAX_MESSAGES = 8
MAX_CHARS = 1500


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]+", "", s).strip()


def dedupe(facts: list[str], existing: list[str]) -> list[str]:
    seen = [_norm(e) for e in existing]
    out: list[str] = []
    for f in facts:
        n = _norm(f)
        if not n or any(n == x or n in x or x in n for x in seen):
            continue
        seen.append(n)
        out.append(f.strip())
    return out


def parse_facts(text: str) -> list[str]:
    m = re.search(r"\[.*\]", text or "", re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    return [str(x).strip()[:300] for x in arr if isinstance(x, str) and x.strip()][:10]


async def extract(settings: Settings, http: httpx.AsyncClient, *, messages: list[dict],
                  existing: list[str], model: str, secrets: list[str]) -> list[str]:
    window = [{"role": m.get("role", "user"), "content": str(m.get("content", ""))[:MAX_CHARS]}
              for m in messages if m.get("role") in ("user", "assistant")][-MAX_MESSAGES:]
    if not any(m["role"] == "user" for m in window):
        return []
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in window)
    try:
        r = await http.post(
            f"{settings.litellm_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.litellm_key}"} if settings.litellm_key
            else {},
            json={"model": model or "agente-padrao", "temperature": 0,
                  "messages": [{"role": "system", "content": PROMPT},
                               {"role": "user", "content": convo}]}, timeout=60.0)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001 - nunca derruba o turno
        log.warning("extracao de memorias falhou: %s", type(exc).__name__)
        return []
    facts = [f for f in parse_facts(content) if not any(s and s in f for s in secrets)]
    return dedupe(facts, existing)
