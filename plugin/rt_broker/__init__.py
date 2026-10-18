"""Plugin rt-broker: toolset `rt_platform` = stubs HTTP que repassam a chamada ao broker.

Contrato com o broker (kernel-harness):
    POST $RT_BROKER_URL
    Authorization: Bearer $RT_BROKER_TOKEN        (token fixo do pool)
    body: {"session_id": "...", "tool": "rt_x", "args": {...}, "meta": {...}}
O handler roda DENTRO do processo do Hermes (nao no shell do agente). O `session_id` vem do
contexto da run (`gateway.session_context.get_session_env("HERMES_SESSION_ID")`, com fallback
para o kwarg `session_id` que o dispatcher do Hermes injeta). A autorizacao real e do broker
(casa session_id com uma run ativa); este lado so transporta.

Quais ferramentas o agente ve: o toolset `rt_platform` precisa estar em
`platform_toolsets.api_server` E o nome da ferramenta precisa estar em
`plugins.entries.rt-broker.settings.tools` do config.yaml do perfil (ausente/vazio = nenhuma).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

TOOLSET = "rt_platform"
PLUGIN_ID = "rt-broker"
MAX_RESPONSE_BYTES = 256 * 1024
DEBUG_META: dict[str, str] = {}


def _obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or []}


_S = {"type": "string"}
_N = {"type": "number"}

# nome -> (descricao ao modelo, schema JSON dos parametros). Placeholders: o contrato final
# de cada ferramenta e do broker (spec 04 6.4); aqui so o formato que o modelo enxerga.
TOOLS: dict[str, tuple[str, dict[str, Any]]] = {
    "rt_echo": (
        "Ferramenta de teste: devolve o que for enviado, ecoado pelo broker.",
        _obj({"text": {**_S, "description": "Texto a ecoar"}}, ["text"]),
    ),
    "rt_sql_read": (
        "Executa uma consulta SQL somente leitura (SELECT/WITH) numa fonte de dados autorizada.",
        _obj(
            {
                "datasource": {**_S, "description": "Rotulo da fonte de dados"},
                "query": {**_S, "description": "SELECT ou WITH"},
                "limit": {"type": "integer", "description": "Maximo de linhas"},
            },
            ["datasource", "query"],
        ),
    ),
    "rt_pix_create": (
        "Cria uma cobranca Pix (exige confirmacao do usuario antes de executar).",
        _obj(
            {
                "amount": {**_S, "description": "Valor decimal em BRL, ex.: '10.50'"},
                "description": _S,
                "reference": {**_S, "description": "Referencia estavel do pedido"},
            },
            ["amount", "reference"],
        ),
    ),
    "rt_payment_status": (
        "Consulta o status de um pagamento.",
        _obj({"payment_id": _S, "reference": _S}),
    ),
    "rt_email_send": (
        "Envia um e-mail (exige confirmacao do usuario antes de executar).",
        _obj(
            {
                "account": {**_S, "description": "Rotulo da conta de e-mail"},
                "to": {"type": "array", "items": _S},
                "subject": _S,
                "body": _S,
            },
            ["to", "subject", "body"],
        ),
    ),
    "rt_http": (
        "Chamada HTTP a API de terceiro (host em allowlist; segredos por {{secret:NOME}} resolvidos no broker).",
        _obj(
            {
                "method": {**_S, "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                "url": _S,
                "headers": {"type": "object"},
                "body": {"type": ["object", "string", "null"]},
            },
            ["method", "url"],
        ),
    ),
    "rt_search_docs": (
        "Busca lexical nos arquivos/documentos do tenant.",
        _obj({"query": _S, "top_k": {"type": "integer"}}, ["query"]),
    ),
    "rt_artifact_put": (
        "Registra um artefato (arquivo, imagem, dataset) gerado, para o usuario baixar.",
        _obj(
            {
                "name": _S,
                "kind": {**_S, "enum": ["file", "image", "dataset", "html"]},
                "content_base64": _S,
                "source_ref": {**_S, "description": "Referencia a conteudo ja no executor/ambiente"},
            },
            ["name", "kind"],
        ),
    ),
    "rt_env_exec": (
        "Executa um comando no ambiente persistente da empresa (quando contratado).",
        _obj({"command": _S, "timeout_s": _N, "cwd": _S}, ["command"]),
    ),
}


def _session_id(kwarg_session_id: str | None) -> str:
    """Prefere o contexto da run (ContextVar do gateway); cai para o kwarg do dispatcher."""
    try:
        from gateway.session_context import get_session_env

        sid = get_session_env("HERMES_SESSION_ID", "")
        if sid:
            return sid
    except Exception:  # fora do gateway (testes, CLI): sem contexto
        pass
    return kwarg_session_id or os.environ.get("HERMES_SESSION_ID", "")


def _meta() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        from gateway.session_context import get_session_env

        for key, var in (
            ("session_key", "HERMES_SESSION_KEY"),
            ("profile", "HERMES_SESSION_PROFILE"),
            ("platform", "HERMES_SESSION_PLATFORM"),
            ("chat_id", "HERMES_SESSION_CHAT_ID"),
        ):
            val = get_session_env(var, "")
            if val:
                out[key] = val
    except Exception:
        pass
    return out


def call_broker(tool: str, args: dict[str, Any], session_id: str, task_id: str | None = None) -> str:
    """POST ao broker; devolve SEMPRE uma string JSON (erro de transporte vira {"error": ...})."""
    url = os.environ.get("RT_BROKER_URL", "").strip()
    token = os.environ.get("RT_BROKER_TOKEN", "").strip()
    if not url or not token:
        return json.dumps({"error": "broker_not_configured"})
    meta = _meta()
    meta.update(DEBUG_META)
    if task_id:
        meta["task_id"] = task_id
    body = json.dumps({"session_id": session_id, "tool": tool, "args": args, "meta": meta}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    timeout = float(os.environ.get("RT_BROKER_TIMEOUT_S", "30"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raw = exc.read(MAX_RESPONSE_BYTES + 1)
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"detail": raw[:500].decode("utf-8", "replace")}
        return json.dumps({"error": f"broker_http_{exc.code}", "broker": payload})
    except Exception as exc:  # rede, timeout, DNS
        return json.dumps({"error": "broker_unreachable", "detail": type(exc).__name__})
    if len(raw) > MAX_RESPONSE_BYTES:
        return json.dumps({"error": "broker_response_too_large"})
    text = raw.decode("utf-8", "replace")
    try:
        json.loads(text)
        return text
    except Exception:
        return json.dumps({"result": text})


def _allowed_tools() -> set[str]:
    """Ferramentas liberadas no perfil ativo (plugins.entries.rt-broker.settings.tools)."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        entry = ((cfg.get("plugins") or {}).get("entries") or {}).get(PLUGIN_ID) or {}
        tools = (entry.get("settings") or {}).get("tools") or []
        return {str(t) for t in tools}
    except Exception:
        logger.exception("rt-broker: falha ao ler lista de ferramentas; nenhuma liberada")
        return set()


def _make_handler(tool: str):
    def handler(args: dict[str, Any], session_id: str | None = None, task_id: str | None = None, **_: Any) -> str:
        sid = _session_id(session_id)
        if os.environ.get("RT_BROKER_DEBUG_META") == "1":  # so em POC/diagnostico
            DEBUG_META.update({"kw_session_id": session_id or "", "ctx_session_id": sid, "pid": str(os.getpid()),
                               "thread": threading.current_thread().name})
        return call_broker(tool, dict(args or {}), sid, task_id)

    handler.__name__ = f"handle_{tool}"
    return handler


def _make_check(tool: str):
    def check() -> bool:
        return tool in _allowed_tools()

    return check


def register(ctx) -> None:
    for name, (description, params) in TOOLS.items():
        ctx.register_tool(
            name=name,
            toolset=TOOLSET,
            schema={"name": name, "description": description, "parameters": params},
            handler=_make_handler(name),
            check_fn=_make_check(name),
            description=description,
        )
