"""Ferramentas de dados: rt_data_describe, rt_sql_read, rt_mongo_find, rt_sql_write,
rt_sql_transaction. Guardrails do kernel-llm preservados e reforcados (SQL guardado)."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time
from decimal import Decimal

from broker import sqlio
from broker.artifacts import json_bytes, put_artifact
from broker.errors import ToolError
from harness.runctx import RunContext

_WRITE_WORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|merge|call|copy|"
    r"vacuum|attach|pragma|exec|execute|into|set)\b", re.I)
_MONGO_BAD = {"$where", "$function", "$accumulator", "$expr"}


_TOKEN = re.compile(r"""'(?:[^']|'')*'|"(?:[^"]|"")*"|--[^\n]*|/\*.*?\*/""", re.S)


def strip_literals(sql: str) -> str:
    """Troca literais/identificadores entre aspas por vazios e remove comentarios, numa so
    passada (ordem importa: '--' dentro de string nao e comentario)."""
    def repl(m: re.Match) -> str:
        t = m.group(0)
        return "''" if t[0] == "'" else ('""' if t[0] == '"' else " ")

    return _TOKEN.sub(repl, sql)


def guard_select(sql: str) -> str:
    """So SELECT/WITH, um unico statement, sem palavra de escrita (fora de literais)."""
    if not sql or not sql.strip():
        raise ToolError("invalid_args", "Informe a consulta SQL.")
    try:
        sqlio.ensure_readonly(sql)
    except ValueError as exc:
        raise ToolError("guardrail", str(exc)) from exc
    bare = strip_literals(sql).strip().rstrip(";").strip()
    if ";" in bare:
        raise ToolError("guardrail", "Apenas uma consulta por chamada.")
    m = _WRITE_WORDS.search(bare)
    if m:
        raise ToolError("guardrail", f"Palavra não permitida em consulta de leitura: {m.group(1)}")
    return sql.strip().rstrip(";")


def json_safe(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (datetime, date, time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(v))} bytes>"
    if isinstance(v, (list, tuple)):
        return [json_safe(x) for x in v]
    if isinstance(v, dict):
        return {str(k): json_safe(x) for k, x in v.items()}
    return str(v)


def pick_datasource(ctx: RunContext, name: str | None) -> dict:
    if not ctx.granted("datasource"):
        raise ToolError("forbidden_target", "Este agente não tem acesso a fontes de dados.")
    items = ctx.cred_list("datasources")
    if not items:
        raise ToolError("unavailable", "Nenhuma fonte de dados configurada para esta empresa.")
    if not name:
        if len(items) == 1:
            return items[0]
        raise ToolError("invalid_args", "Informe qual fonte de dados usar.")
    for d in items:
        if d.get("name") == name:
            return d
    raise ToolError("invalid_args", f"Fonte de dados '{name}' não encontrada.")


def _cache_key(*parts) -> str:
    return json.dumps(parts, sort_keys=True, default=str)


async def describe(svc, ctx: RunContext, args: dict):
    if not ctx.granted("datasource"):
        raise ToolError("forbidden_target", "Este agente não tem acesso a fontes de dados.")
    items = ctx.cred_list("datasources")
    wanted = args.get("datasource")
    out = []
    for d in items:
        if wanted and d.get("name") != wanted:
            continue
        try:
            tables = await sqlio.list_tables(d)
        except Exception as exc:  # noqa: BLE001
            tables = f"catálogo indisponível ({type(exc).__name__})"
        out.append({"datasource": d.get("name"), "kind": d.get("kind"), "tables": tables})
    return {"datasources": out}


async def sql_read(svc, ctx: RunContext, args: dict):
    ds = pick_datasource(ctx, args.get("datasource"))
    sql = guard_select(str(args.get("query") or ""))
    title = str(args.get("title") or "Consulta")[:120]
    limit = int(args.get("limit") or svc.s.sql_max_rows)
    limit = max(1, min(limit, svc.s.sql_max_rows))
    key = _cache_key("sql", ds.get("name"), sql, limit)
    if key in ctx.read_cache:
        res = dict(ctx.read_cache[key])
        res["AVISO"] = ("Esta mesma consulta já foi executada neste turno; use o resultado "
                        "anterior em vez de repetir.")
        return res
    try:
        cols, rows = await sqlio.execute_query(ds, sql, limit, svc.s.sql_read_timeout_s + 10)
    except ToolError:
        raise
    except TimeoutError as exc:
        raise ToolError("tool_error", "A consulta demorou demais e foi cancelada.") from exc
    except Exception as exc:  # noqa: BLE001
        raise ToolError("tool_error", f"Erro na consulta: {type(exc).__name__}: {exc}"[:300]) from exc
    rows = [[json_safe(c) for c in r] for r in rows]
    schema = [{"name": c["name"], "type": c["type"]} for c in cols]
    n_prev = svc.s.artifact_preview_rows
    desc = await put_artifact(
        svc, ctx, kind="dataset", title=title,
        data=json_bytes({"columns": schema, "rows": rows}), extension="json",
        content_type="application/json", schema=schema, preview=rows[:n_prev],
        row_count=len(rows))
    res = {**desc, "truncated_at_limit": len(rows) >= limit}
    ctx.read_cache[key] = res
    return res


def _mongo_check(node) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in _MONGO_BAD:
                raise ToolError("guardrail", f"Operador {k} não é permitido.")
            _mongo_check(v)
    elif isinstance(node, list):
        for v in node:
            _mongo_check(v)


def _json_arg(val, field: str):
    if val in (None, ""):
        return None
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError as exc:
            raise ToolError("invalid_args", f"{field} não é um JSON válido.") from exc
    return val


async def mongo_find(svc, ctx: RunContext, args: dict):
    ds = pick_datasource(ctx, args.get("datasource"))
    if ds.get("kind") != "mongodb":
        raise ToolError("invalid_args", "A fonte informada não é MongoDB.")
    flt = _json_arg(args.get("filter"), "filter") or {}
    proj = _json_arg(args.get("projection"), "projection")
    _mongo_check(flt)
    limit = max(1, min(int(args.get("limit") or 1000), svc.s.sql_max_rows))
    try:
        cols, rows = await sqlio.query_mongo(ds, str(args.get("collection") or ""), flt, proj,
                                             limit)
    except Exception as exc:  # noqa: BLE001
        raise ToolError("tool_error", f"Erro na consulta: {type(exc).__name__}"[:200]) from exc
    return await put_artifact(
        svc, ctx, kind="dataset", title=str(args.get("title") or "Consulta"),
        data=json_bytes({"columns": cols, "rows": rows}), extension="json",
        content_type="application/json", schema=cols,
        preview=rows[: svc.s.artifact_preview_rows], row_count=len(rows))


def _write_tables(ctx: RunContext) -> list[str]:
    tables = ctx.limits.get("write_tables") or []
    if not tables:
        raise ToolError("guardrail", "Este agente não tem tabelas liberadas para escrita.")
    return list(tables)


def _short(sql: str, n: int = 300) -> str:
    return re.sub(r"\s+", " ", sql).strip()[:n]


async def sql_write(svc, ctx: RunContext, args: dict):
    ds = pick_datasource(ctx, args.get("datasource"))
    stmt = str(args.get("statement") or "")
    tables = _write_tables(ctx)
    try:
        table = sqlio.validate_write(stmt, tables)
    except ValueError as exc:
        raise ToolError("guardrail", str(exc)) from exc
    await svc.confirm(ctx, kind="sql_write", risk="high",
                      summary=f"Gravar na tabela {table} (fonte {ds.get('name')}): {_short(stmt)}",
                      details={"datasource": ds.get("name"), "table": table,
                               "statement": _short(stmt, 1000)})
    try:
        table, rows, returned = await sqlio.execute_write(ds, stmt, tables)
    except ValueError as exc:
        raise ToolError("guardrail", str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise ToolError("tool_error", f"Erro ao gravar: {type(exc).__name__}"[:200]) from exc
    ctx.read_cache.clear()
    return {"table": table, "affected_rows": rows, "returned": json_safe(returned)}


async def sql_transaction(svc, ctx: RunContext, args: dict):
    ds = pick_datasource(ctx, args.get("datasource"))
    stmts = _json_arg(args.get("statements"), "statements")
    if not isinstance(stmts, list) or not all(isinstance(s, str) for s in stmts):
        raise ToolError("invalid_args", "statements deve ser uma lista de comandos SQL.")
    if not stmts or len(stmts) > 50:
        raise ToolError("guardrail", "A transação precisa de 1 a 50 comandos.")
    tables = _write_tables(ctx)
    try:
        touched = [sqlio.validate_write(s, tables) for s in stmts]
    except ValueError as exc:
        raise ToolError("guardrail", str(exc)) from exc
    await svc.confirm(ctx, kind="sql_transaction", risk="high",
                      summary=(f"Gravar {len(stmts)} comandos atômicos na fonte {ds.get('name')} "
                               f"(tabelas: {', '.join(sorted(set(touched)))})"),
                      details={"datasource": ds.get("name"),
                               "statements": [_short(s, 400) for s in stmts]})
    try:
        res = await sqlio.execute_transaction(ds, stmts, tables)
    except ValueError as exc:
        raise ToolError("guardrail", str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise ToolError("tool_error", f"Transação desfeita: {type(exc).__name__}"[:200]) from exc
    ctx.read_cache.clear()
    return {"committed": True, "results": json_safe(res)}
