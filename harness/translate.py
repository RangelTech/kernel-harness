"""Traducao de eventos Hermes -> contrato do produto (spec 4.2). Funcao pura/sem I/O."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from harness.hermes_client import HermesEvent

# --------------------------------------------------------------- rotulos pt-BR
# Nunca expor nomes internos (rt_*, hermes, kernel, harness). Desconhecida -> generico.
TOOL_LABELS: dict[str, tuple[str, str]] = {
    # nome da ferramenta -> (kind, rotulo)
    "rt_data_describe": ("tool", "Consultando as fontes de dados"),
    "rt_sql_read": ("tool", "Consultando dados"),
    "rt_mongo_find": ("tool", "Consultando dados"),
    "rt_sql_write": ("tool", "Gravando dados"),
    "rt_sql_transaction": ("tool", "Gravando dados"),
    "rt_pix_create": ("tool", "Gerando cobrança Pix"),
    "rt_payment_status": ("tool", "Conferindo pagamento"),
    "rt_mp_read": ("tool", "Consultando pagamentos"),
    "rt_email_list": ("tool", "Lendo e-mails"),
    "rt_email_send": ("tool", "Enviando e-mail"),
    "rt_google_calendar_list": ("tool", "Consultando a agenda"),
    "rt_google_calendar_create": ("tool", "Criando compromisso na agenda"),
    "rt_google_sheets_read": ("tool", "Lendo planilha"),
    "rt_google_sheets_write": ("tool", "Atualizando planilha"),
    "rt_google_sheets_create": ("tool", "Criando planilha"),
    "rt_ms_calendar_list": ("tool", "Consultando a agenda"),
    "rt_ms_calendar_create": ("tool", "Criando compromisso na agenda"),
    "rt_http": ("tool", "Consultando um serviço externo"),
    "rt_search_docs": ("tool", "Buscando nos documentos"),
    "rt_memory_recall": ("tool", "Lembrando de conversas anteriores"),
    "rt_artifact_put": ("tool", "Preparando o arquivo"),
    "rt_image_generate": ("tool", "Gerando imagem"),
    "rt_exec": ("tool", "Processando os dados"),
    "rt_env_status": ("tool", "Verificando o ambiente"),
    "rt_env_start": ("tool", "Iniciando o ambiente"),
    "rt_env_expose": ("tool", "Publicando o aplicativo"),
    "rt_env_domain": ("tool", "Configurando o domínio"),
    "rt_env_backup": ("tool", "Fazendo backup do ambiente"),
    "web_search": ("tool", "Pesquisando na web"),
    "web_extract": ("tool", "Lendo uma página da web"),
    "vision_analyze": ("tool", "Analisando a imagem"),
    "memory": ("tool", "Consultando a memória"),
    "session_search": ("tool", "Revisando a conversa"),
    "skills_list": ("tool", "Consultando as habilidades disponíveis"),
    "skill_manage": ("tool", "Registrando um aprendizado"),
    "todo_list": ("plan", "Planejando os próximos passos"),
    "delegate_task": ("plan", "Dividindo o trabalho em subtarefas"),
}
DELEGATE_TOOLS = frozenset({"delegate_task"})
GENERIC_LABEL = "Executando uma ação"
INTERNAL_WORDS = re.compile(r"hermes|harness|kernel|rt_|broker|profile|perfil interno", re.I)


def tool_label(tool: str, preview: str = "", system_skills: frozenset[str] = frozenset()
               ) -> tuple[str, str]:
    if tool == "skill_view":
        name = _skill_name_from_preview(preview)
        if name and name not in system_skills and not INTERNAL_WORDS.search(name):
            return "skill", f"Consultando: {name}"
        return "skill", "Consultando uma habilidade"
    kind, label = TOOL_LABELS.get(tool, ("tool", GENERIC_LABEL))
    return kind, label


def _skill_name_from_preview(preview: str) -> str | None:
    if not preview:
        return None
    try:
        obj = json.loads(preview)
        if isinstance(obj, dict):
            n = obj.get("name") or obj.get("skill")
            if isinstance(n, str) and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", n):
                return n
    except (json.JSONDecodeError, TypeError):
        pass
    m = re.search(r"""["']?(?:name|skill)["']?\s*[:=]\s*["']([a-z0-9][a-z0-9._-]{0,63})["']""",
                  preview)
    return m.group(1) if m else None


# ------------------------------------------------------- artefatos na saida
_ART_OBJ = re.compile(r"\{[^{}]*\"artifact_id\"[^{}]*\}")


def find_artifact_descriptors(text: str) -> list[dict]:
    """Descriptors {artifact_id, kind, title} em JSON de saida de ferramenta (nivel 0/1/lista)."""
    found: list[dict] = []

    def visit(node, depth=0) -> None:
        if depth > 3:
            return
        if isinstance(node, dict):
            if "artifact_id" in node and node.get("kind"):
                found.append({"artifact_id": str(node["artifact_id"]), "kind": str(node["kind"]),
                              "title": str(node.get("title") or "Arquivo gerado")})
                return
            for v in node.values():
                visit(v, depth + 1)
        elif isinstance(node, list):
            for v in node:
                visit(v, depth + 1)

    try:
        visit(json.loads(text))
    except (json.JSONDecodeError, TypeError):
        for m in _ART_OBJ.finditer(text or ""):  # preview truncado: tenta objetos soltos
            try:
                visit(json.loads(m.group(0)))
            except json.JSONDecodeError:
                continue
    return found


# ---------------------------------------------------------------- <think>
class ThinkFilter:
    """Remove <think>...</think> do stream, mesmo cortado entre deltas."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self.inside = False
        self.buf = ""

    def feed(self, chunk: str) -> str:
        self.buf += chunk
        out = []
        while self.buf:
            if self.inside:
                i = self.buf.find(self.CLOSE)
                if i < 0:
                    keep = len(self.CLOSE) - 1
                    self.buf = self.buf[-keep:] if len(self.buf) > keep else self.buf
                    break
                self.buf = self.buf[i + len(self.CLOSE):]
                self.inside = False
            else:
                i = self.buf.find(self.OPEN)
                if i >= 0:
                    out.append(self.buf[:i])
                    self.buf = self.buf[i + len(self.OPEN):]
                    self.inside = True
                    continue
                # retem sufixo que pode ser inicio de <think>
                hold = 0
                for k in range(min(len(self.OPEN) - 1, len(self.buf)), 0, -1):
                    if self.OPEN.startswith(self.buf[-k:]):
                        hold = k
                        break
                out.append(self.buf[: len(self.buf) - hold])
                self.buf = self.buf[len(self.buf) - hold:]
                break
        return "".join(out)

    def flush(self) -> str:
        rest = "" if self.inside else self.buf
        self.buf = ""
        return rest


def strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


# ------------------------------------------------------------- "pensando"
THINKING_MAX = 280
_THINK_TAGS = re.compile(r"</?(?:think|thinking|reasoning)>", re.I)
_TOOL_NAME = re.compile(r"\brt_[a-z0-9_]+\b", re.I)
_INTERNAL_NAME = re.compile(r"\b(?:hermes|harness|kernel|broker)\b", re.I)


def friendly_thinking(text: str, limit: int = THINKING_MAX) -> str:
    """Resumo do raciocinio em linguagem de produto: sem tags, sem nomes internos, curto."""
    t = _THINK_TAGS.sub(" ", text or "")
    t = _TOOL_NAME.sub("uma ferramenta", t)
    t = _INTERNAL_NAME.sub("o sistema", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0].rstrip(",;:.") + "…"
    return t


# ----------------------------------------------------------------- tradutor
@dataclass
class Outcome:
    kind: str  # completed | failed | cancelled | interrupted | partial
    text: str = ""
    usage: dict = field(default_factory=dict)
    runtime: dict = field(default_factory=dict)
    error: str = ""


@dataclass
class Signals:
    events: list[tuple[str, dict]] = field(default_factory=list)
    outcome: Outcome | None = None
    deny_requests: list[str | None] = field(default_factory=list)
    tool_started: int = 0


class EventTranslator:
    def __init__(self, system_skills: frozenset[str] = frozenset()):
        self.system_skills = system_skills
        self.think = ThinkFilter()
        self.text_parts: list[str] = []
        self.pending_tools: dict[str, list[tuple[str, str]]] = {}
        self.emitted_artifacts: set[str] = set()
        self.tool_count = 0
        self.denied_commands = 0
        self.last_thinking = ""
        # subagentes: chave -> lote (delegation_id); contagem derivada dos eventos de delegacao
        self._sub_active: set[str] = set()
        self._sub_seen: set[str] = set()
        self._sub_expected: dict[str, int] = {}
        self._sub_started: dict[str, int] = {}
        self._sub_batch: dict[str, str] = {}
        self._sub_last: tuple[int, int] | None = None
        self._sub_seq = 0

    # ---- contador de subagentes ativos/total (contrato SSE `subagents`)
    def subagent_counts(self) -> tuple[int, int]:
        total = sum(max(self._sub_expected.get(b, 0), self._sub_started.get(b, 0))
                    for b in set(self._sub_expected) | set(self._sub_started))
        return len(self._sub_active), total

    def _subagents_event(self) -> list[tuple[str, dict]]:
        active, total = self.subagent_counts()
        if (active, total) == self._sub_last:
            return []
        self._sub_last = (active, total)
        return [("subagents", {"active": active, "total": total})]

    def _sub_key(self, d: dict) -> tuple[str, str]:
        batch = str(d.get("delegation_id") or d.get("parent_id") or "-")
        ident = d.get("subagent_id") or d.get("child_session_id")
        if ident is None and d.get("task_index") is not None:
            ident = f"{batch}#{d.get('task_index')}"
        if ident is None:
            self._sub_seq += 1
            ident = f"anon#{self._sub_seq}"
        return batch, str(ident)

    def translate(self, ev: HermesEvent) -> Signals:  # noqa: C901 - tabela de eventos
        sig = Signals()
        d = ev.data
        name = ev.event
        if name == "message.delta":
            t = self.think.feed(d.get("delta") or "")
            if t:
                self.text_parts.append(t)
                sig.events.append(("token", {"text": t}))
        elif name == "message.interim":
            if not d.get("already_streamed"):
                t = self.think.feed(d.get("text") or "")
                if t:
                    self.text_parts.append(t)
                    sig.events.append(("token", {"text": t}))
        elif name == "reasoning.available":
            # resumo curto e sem nomes internos; o texto bruto do modelo nunca passa
            t = friendly_thinking(d.get("text") or "")
            if t and t != self.last_thinking:
                self.last_thinking = t
                sig.events.append(("thinking", {"text": t}))
        elif name == "tool.started":
            tool = d.get("tool") or ""
            preview = d.get("preview") or ""
            kind, label = tool_label(tool, preview if isinstance(preview, str) else
                                     json.dumps(preview), self.system_skills)
            self.pending_tools.setdefault(tool, []).append((kind, label))
            self.tool_count += 1
            sig.tool_started = 1
            sig.events.append(("step", {"kind": kind, "label": label, "status": "start"}))
        elif name == "tool.completed":
            tool = d.get("tool") or ""
            if tool in DELEGATE_TOOLS and self._sub_active:
                # a delegacao terminou: nenhum filho segue ativo (evita contador preso)
                self._sub_active.clear()
                sig.events.extend(self._subagents_event())
            stack = self.pending_tools.get(tool) or []
            kind, label = stack.pop(0) if stack else tool_label(tool, "", self.system_skills)
            status = "error" if d.get("error") else "done"
            sig.events.append(("step", {"kind": kind, "label": label, "status": status}))
            preview = d.get("preview")
            if preview and not d.get("error"):
                text = preview if isinstance(preview, str) else json.dumps(preview)
                for art in find_artifact_descriptors(text):
                    if art["artifact_id"] not in self.emitted_artifacts:
                        self.emitted_artifacts.add(art["artifact_id"])
                        sig.events.append(("artifact", art))
        elif name == "subagent.start":
            batch, key = self._sub_key(d)
            if key not in self._sub_seen:
                self._sub_seen.add(key)
                self._sub_started[batch] = self._sub_started.get(batch, 0) + 1
            self._sub_batch[key] = batch
            try:
                n = int(d.get("task_count") or 0)
            except (TypeError, ValueError):
                n = 0
            if n > 0:
                self._sub_expected[batch] = max(self._sub_expected.get(batch, 0), min(n, 50))
            self._sub_active.add(key)
            sig.events.append(("agent", {"name": _short(d.get("goal") or "subtarefa"),
                                         "status": "start"}))
            sig.events.extend(self._subagents_event())
        elif name == "subagent.complete":
            _, key = self._sub_key(d)
            if key in self._sub_active:
                self._sub_active.discard(key)
            elif self._sub_active and key.startswith("anon#"):
                self._sub_active.pop()  # sem id: o evento so fecha um filho em aberto
            sig.events.append(("agent", {"name": _short(d.get("goal") or "subtarefa"),
                                         "status": "done"}))
            sig.events.extend(self._subagents_event())
        elif name == "approval.request":
            # comandos nunca sao aprovados pelo kernel: deny automatico (R-04)
            self.denied_commands += 1
            sig.deny_requests.append(d.get("request_id"))
        elif name in ("approval.responded", "run.steered", "replay.truncated"):
            pass
        elif name == "run.completed":
            self.text_parts.append(self.think.flush())
            output = strip_think(d.get("output") or "")
            text = output or strip_think("".join(self.text_parts))
            if d.get("interrupted"):
                kind = "interrupted"
            elif d.get("partial") or d.get("completed") is False:
                kind = "partial"
            else:
                kind = "completed"
            sig.outcome = Outcome(kind, text, d.get("usage") or {}, d.get("runtime") or {})
        elif name == "run.failed":
            sig.outcome = Outcome("failed", error=str(d.get("error") or "")[:500])
        elif name == "run.cancelled":
            sig.outcome = Outcome("cancelled")
        elif name == "run.interrupted":
            sig.outcome = Outcome("interrupted")
        # eventos desconhecidos: ignorados (o produto tambem ignora)
        return sig


def _short(s: str, n: int = 60) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s[:n] if not INTERNAL_WORDS.search(s) else "subtarefa"
