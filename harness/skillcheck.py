"""Validacao de pacote de skills (spec 3.1) e de skill de usuario (3.5)."""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field

import yaml

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
USER_NAME_RE = re.compile(r"^u-[a-z0-9][a-z0-9._-]{0,61}$")
MAX_FILE = 200 * 1024
MAX_SKILL_MD_CHARS = 100_000
MAX_PACKAGE = 2 * 1024 * 1024
MAX_FILES = 100
MAX_USER_SKILL = 32 * 1024
SCRIPT_EXT = {".py", ".sh", ".js"}

SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"(?:APP_USR|TEST)-[0-9a-f]{8,}-[0-9a-f-]{8,}"),
    re.compile(r"(?i)\b(?:password|passwd|senha|secret|api[_-]?key|token)\b\s*[:=]\s*"
               r"['\"]?(?![{<$]|\*|x{3,})[^\s'\"]{8,}"),
]

DEFAULT_TOOL_CATALOG = {
    "rt_data_describe", "rt_sql_read", "rt_mongo_find", "rt_sql_write", "rt_sql_transaction",
    "rt_pix_create", "rt_payment_status", "rt_mp_read", "rt_email_list", "rt_email_send",
    "rt_google_calendar_list", "rt_google_calendar_create", "rt_google_sheets_read",
    "rt_google_sheets_write", "rt_google_sheets_create", "rt_ms_calendar_list",
    "rt_ms_calendar_create", "rt_http", "rt_search_docs", "rt_memory_recall", "rt_artifact_put",
    "rt_image_generate", "rt_exec", "rt_env_status", "rt_env_start", "rt_env_expose",
    "rt_env_domain", "rt_env_backup",
    # ferramentas nativas liberadas por manifesto
    "web_search", "web_extract", "vision_analyze",
}


@dataclass
class Problem:
    code: str
    message: str
    path: str | None = None
    line: int | None = None
    severity: str = "error"

    def dict(self) -> dict:
        return {"code": self.code, "message": self.message, "path": self.path,
                "line": self.line, "severity": self.severity}


@dataclass
class Report:
    problems: list[Problem] = field(default_factory=list)

    def add(self, *a, **k) -> None:
        self.problems.append(Problem(*a, **k))

    @property
    def ok(self) -> bool:
        return not any(p.severity == "error" for p in self.problems)


def find_secret(text: str) -> tuple[int, str] | None:
    for pat in SECRET_PATTERNS:
        m = pat.search(text)
        if m:
            return text.count("\n", 0, m.start()) + 1, pat.pattern[:30]
    return None


def split_frontmatter(text: str) -> tuple[dict | None, str, str | None]:
    """(frontmatter, corpo, erro)."""
    if not text.startswith("---"):
        return None, text, "frontmatter ausente (o arquivo deve comecar com ---)"
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", text, re.S)
    if not m:
        return None, text, "frontmatter nao fechado com ---"
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        return None, text, f"YAML invalido: {str(exc)[:120]}"
    if not isinstance(fm, dict):
        return None, text, "frontmatter deve ser um mapa YAML"
    return fm, m.group(2), None


def _norm_path(path: str) -> str | None:
    p = path.replace("\\", "/")
    if not p or p.startswith("/") or re.match(r"^[A-Za-z]:", p):
        return None
    parts = p.split("/")
    if any(x in ("", ".", "..") or x.startswith(".") for x in parts):
        return None
    return p


def validate_package(files: list[dict], manifest: dict | None = None,
                     available_tools: set[str] | None = None,
                     extra_secret_values: list[str] | None = None) -> Report:  # noqa: C901
    rep = Report()
    texts: dict[str, str] = {}
    total = 0
    if len(files) > MAX_FILES:
        rep.add("too_many_files", f"{len(files)} arquivos (maximo {MAX_FILES})")
    for f in files:
        raw_path = f.get("path", "")
        path = _norm_path(raw_path)
        if path is None:
            rep.add("bad_path", f"caminho invalido: {raw_path!r}", raw_path)
            continue
        if f.get("content_b64") is not None:
            try:
                data = base64.b64decode(f["content_b64"], validate=True)
                content = data.decode("utf-8")
            except Exception:  # noqa: BLE001
                rep.add("bad_encoding", "arquivo nao e UTF-8 valido", path)
                continue
        else:
            content = f.get("content", "")
            data = content.encode("utf-8", errors="replace")
        if "\x00" in content:
            rep.add("bad_encoding", "arquivo contem bytes nulos", path)
            continue
        total += len(data)
        if len(data) > MAX_FILE:
            rep.add("file_too_large", f"{len(data)} bytes (maximo {MAX_FILE})", path)
        if path in texts:
            rep.add("bad_path", "caminho duplicado", path)
        texts[path] = content
        if not _ext_ok(path):
            rep.add("bad_extension", "extensao/pasta nao permitida", path)
        sec = find_secret(content)
        if sec:
            rep.add("secret_detected", "possivel segredo no arquivo", path, sec[0])
        for v in extra_secret_values or []:
            if v and v in content:
                rep.add("secret_detected", "valor de credencial no arquivo", path)
                break
    if total > MAX_PACKAGE:
        rep.add("package_too_large", f"{total} bytes (maximo {MAX_PACKAGE})")
    if "AGENTE.md" not in texts:
        rep.add("missing_agent_md", "AGENTE.md e obrigatorio")

    names: dict[str, str] = {}
    for path, content in texts.items():
        m = re.fullmatch(r"skills/([^/]+)/SKILL\.md", path)
        if not m:
            continue
        folder = m.group(1)
        if len(content) > MAX_SKILL_MD_CHARS:
            rep.add("file_too_large", "SKILL.md acima de 100.000 caracteres", path)
        fm, body, err = split_frontmatter(content)
        if err:
            rep.add("bad_frontmatter", err, path, 1)
            continue
        name = fm.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name) or len(name) > 64:
            rep.add("bad_name", "name invalido (use ^[a-z0-9][a-z0-9._-]*$, ate 64)", path, 1)
            continue
        desc = fm.get("description")
        if not isinstance(desc, str) or not desc.strip() or len(desc) > 1024:
            rep.add("bad_frontmatter", "description obrigatoria (ate 1024 caracteres)", path, 1)
        if name != folder:
            rep.add("bad_name", f"name '{name}' difere da pasta '{folder}'", path, 1, "warning")
        if name in names:
            rep.add("duplicate_name", f"skill '{name}' repetida (tambem em {names[name]})", path)
        names[name] = path
        for ref in re.findall(r"\((references/[^)\s#]+)\)", body):
            if f"skills/{folder}/{ref}" not in texts:
                rep.add("broken_reference", f"referencia inexistente: {ref}", path)

    tools = set((manifest or {}).get("tools") or [])
    catalog = available_tools if available_tools is not None else DEFAULT_TOOL_CATALOG
    for t in sorted(tools - catalog):
        rep.add("unknown_tool", f"ferramenta '{t}' nao existe ou nao esta disponivel")
    return rep


def _ext_ok(path: str) -> bool:
    if path == "AGENTE.md":
        return True
    if re.fullmatch(r"skills/[^/]+/SKILL\.md", path):
        return True
    if re.fullmatch(r"skills/[^/]+/references/.+\.md", path):
        return True
    m = re.fullmatch(r"skills/[^/]+/(scripts|templates)/.+", path)
    if m:
        ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
        if m.group(1) == "scripts":
            return ext in SCRIPT_EXT
        return ext in {".md", ".txt", ".html", ".json", ".csv", ".tmpl", ".j2"}
    return False


# ------------------------------------------------------- skill de usuario
FORBIDDEN_FM_KEYS = ("requires_tools", "requires_toolsets", "required_environment_variables",
                     "required_credential_files", "fallback_for_tools", "prerequisites")


def validate_user_skill(name: str, content: str, *, reserved_names: set[str],
                        secret_values: list[str]) -> tuple[str | None, str, str | None]:
    """(nome_final, conteudo_final, erro). Normaliza o prefixo `u-` quando falta."""
    final = name if name.startswith("u-") else f"u-{name}"
    if not USER_NAME_RE.match(final):
        return None, content, "nome invalido"
    if final in reserved_names or name in reserved_names:
        return None, content, "nome colide com skill de sistema/empresa"
    if len(content.encode("utf-8")) > MAX_USER_SKILL:
        return None, content, "skill acima de 32 KiB"
    fm, body, err = split_frontmatter(content)
    if err:
        return None, content, err
    if not isinstance(fm.get("description"), str) or not fm["description"].strip():
        return None, content, "description obrigatoria"
    meta = ((fm.get("metadata") or {}).get("hermes") or {}) if isinstance(
        fm.get("metadata"), dict) else {}
    for k in FORBIDDEN_FM_KEYS:
        if k in fm or k in meta:
            return None, content, f"skill de usuario nao pode declarar '{k}' (nao amplia capacidade)"
    if re.search(r"!`[^`]+`", body):
        return None, content, "shell inline nao permitido"
    if find_secret(content):
        return None, content, "possivel segredo no conteudo"
    for v in secret_values:
        if v and v in content:
            return None, content, "valor de credencial no conteudo"
    if fm.get("name") != final:
        fm["name"] = final
        content = "---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False) + "---\n" + body
    return final, content, None
