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
    "rt_confirm": (
        "Pede ao usuario, em um cartao de confirmacao, autorizacao ANTES de uma acao quando a regra do "
        "template mandar confirmar. Se negado, nao execute a acao.",
        _obj({"summary": {**_S, "description": "Uma frase: o que vai acontecer"},
              "risk": {"type": "string", "enum": ["low", "medium", "high"]},
              "topic": {**_S, "description": "Assunto curto, ex.: grafico"}}, ["summary"]),
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
    "rt_executor_run": (
        "Roda um script Python num ambiente isolado SEM internet (pandas, matplotlib, pypdf, reportlab). "
        "Dados entram por `inputs` (nome do arquivo -> texto, lidos em /in/<nome>); arquivos gravados "
        "em /out (png, csv, pdf, xlsx...) viram artefatos na conversa. Use para graficos, calculos e PDFs.",
        _obj({"script": {**_S, "description": "Codigo Python completo"},
              "inputs": {"type": "object", "description": "nome do arquivo -> conteudo texto"},
              "attachments": {"type": "array", "items": _S,
                              "description": "Nomes de anexos da conversa a ler em /in/<nome>"},
              "timeout_s": _N}, ["script"]),
    ),
    "rt_env_status": ("Estado do ambiente da empresa, apps publicados e consumo.", _obj({})),
    "rt_env_start": ("Inicia o ambiente da empresa (se estiver parado).", _obj({})),
    "rt_env_stop": ("Para o ambiente da empresa (pede confirmacao).", _obj({})),
    "rt_env_backup": ("Faz um backup do ambiente da empresa agora.", _obj({})),
    "rt_env_expose": (
        "Publica um servico do ambiente na internet (HTTPS, subdominio da empresa). Pede confirmacao.",
        _obj({"app": {**_S, "description": "Nome curto do app (minusculas, numeros, hifen)"},
              "port": {"type": "integer", "description": "Porta onde o servico escuta (1024-65535)"}},
             ["app", "port"]),
    ),
    "rt_env_unexpose": ("Tira um app da internet (pede confirmacao).", _obj({"app": _S}, ["app"])),
    "rt_env_domain_add": (
        "Liga um dominio proprio do cliente a um app publicado; devolve os registros DNS (TXT e CNAME).",
        _obj({"host": _S, "app": _S}, ["host", "app"]),
    ),
    "rt_env_domain_verify": ("Verifica o DNS de um dominio proprio ja adicionado.", _obj({"host": _S}, ["host"])),
    "rt_schedule_create": (
        "Cria uma rotina recorrente da plataforma: no horario, o agente e acordado com a instrucao salva. "
        "cron de 5 campos (ex.: '0 8 * * *' = todo dia as 8h). Pede confirmacao.",
        _obj({"name": _S, "cron": _S, "timezone": {**_S, "description": "Padrao America/Sao_Paulo"},
              "instruction": {**_S, "description": "O que o agente deve fazer a cada disparo (texto completo)"}},
             ["name", "cron", "instruction"]),
    ),
    "rt_schedule_list": ("Lista as rotinas agendadas.", _obj({})),
    "rt_schedule_pause": ("Pausa (ou retoma com resume=true) uma rotina.", _obj({"id": _S, "resume": {"type": "boolean"}}, ["id"])),
    "rt_schedule_delete": ("Apaga uma rotina agendada (pede confirmacao).", _obj({"id": _S}, ["id"])),
    "rt_data_describe": (
        "Lista tabelas e colunas de uma fonte de dados autorizada (use antes de consultar).",
        _obj({"datasource": {**_S, "description": "Rotulo da fonte (opcional se houver uma so)"}}),
    ),
    "rt_mongo_find": (
        "Consulta documentos de uma colecao MongoDB autorizada (somente leitura).",
        _obj({"datasource": _S, "collection": _S, "filter": {"type": ["object", "string"]},
              "projection": {"type": ["object", "string", "null"]}, "limit": {"type": "integer"},
              "title": _S}, ["collection"]),
    ),
    "rt_sql_write": (
        "Executa UMA instrucao de escrita (INSERT/UPDATE/DELETE) em tabela permitida. Pede confirmacao.",
        _obj({"datasource": _S, "statement": _S}, ["statement"]),
    ),
    "rt_sql_transaction": (
        "Executa varias instrucoes de escrita numa transacao so. Pede confirmacao.",
        _obj({"datasource": _S, "statements": {"type": "array", "items": _S}}, ["statements"]),
    ),
    "rt_mp_read": (
        "Busca pagamentos no Mercado Pago (somente leitura): filtre por referencia ou status.",
        _obj({"reference": _S, "status": _S, "limit": {"type": "integer"}}),
    ),
    "rt_email_list": (
        "Lista e-mails recentes da caixa de entrada de uma conta da empresa.",
        _obj({"account": {**_S, "description": "Rotulo da conta"}, "folder": _S,
              "limit": {"type": "integer"}}),
    ),
    "rt_memory_recall": (
        "Recupera memorias/preferencias que o usuario ja informou, relevantes a uma consulta.",
        _obj({"query": _S}, ["query"]),
    ),
    "rt_env_upload": (
        "Copia um anexo da conversa (qualquer arquivo: planilha, imagem, audio, video, pdf...) para o "
        "ambiente da empresa (padrao /workspace/inbox/<nome>) para trabalhar nele com o terminal. "
        "Tambem copia arquivos que a empresa colocou no pacote do template: use `package_file` "
        "(ex.: arquivos/cardapio.pdf, ou * para todos); chegam em /workspace/pacote/.",
        _obj({"attachment": {**_S, "description": "Nome do anexo da conversa"},
              "package_file": {**_S, "description": "Arquivo do pacote do template: arquivos/<nome> ou *"},
              "path": _S}),
    ),
    "rt_env_download": (
        "Entrega ao usuario um arquivo do ambiente (qualquer tipo): vira artefato com link na conversa.",
        _obj({"path": {**_S, "description": "Caminho completo, ex.: /workspace/out/relatorio.xlsx"}, "name": _S},
             ["path"]),
    ),
    "rt_env_exec": (
        "Terminal do ambiente da empresa: roda um comando bash (instalar programas, escrever e rodar "
        "scripts Python, subir servicos, chamar APIs com internet). Para usar uma chave/token no codigo, "
        "liste o NOME em `secrets` (ex.: MERCADO_PAGO_TOKEN): ele chega como variavel de ambiente "
        "(os.environ) e o valor nunca aparece para voce.",
        _obj({"command": _S, "timeout_s": _N, "cwd": _S,
              "secrets": {"type": "array", "items": _S}}, ["command"]),
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
    # O broker pode demorar de verdade: executor (ate 120 s + folga), exec no ambiente e espera de aprovacao
    # (120 s). O limite real e o do turno (1800 s) no kernel; 30 s derrubava essas chamadas com broker_unreachable.
    timeout = float(os.environ.get("RT_BROKER_TIMEOUT_S", "1830"))
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
