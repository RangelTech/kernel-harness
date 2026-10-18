"""SQL/Mongo do tenant: portado de kernel-llm/app/datasources.py (nao importado).

Guardrails preservados: leitura so SELECT/WITH (`ensure_readonly`), teto de linhas, connect
10 s / leitura 60 s; escrita com `validate_write` (1 statement, so DML, sem DDL, WHERE em
UPDATE/DELETE, tabela na allowlist) e transacao atomica (ate 50 statements, `{{returned:N}}`).
Drivers sincronos rodam em thread (`asyncio.to_thread`); imports preguicosos.
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3

_READONLY = re.compile(r"^\s*(--[^\n]*\n|\s)*(select|with)\b", re.IGNORECASE)


def ensure_readonly(sql: str) -> None:
    if not _READONLY.match(sql or ""):
        raise ValueError("apenas consultas SELECT/WITH são permitidas nesta tool")


def _rows(rows) -> list[list]:
    return [list(r) for r in rows]


# ------------------------------------------------------------------ leitura
def _query_postgresql(config, secret, sql, max_rows):
    import psycopg

    conninfo = psycopg.conninfo.make_conninfo(
        host=config.get("host", "localhost"), port=int(config.get("port", 5432)),
        dbname=config.get("database", ""), user=config.get("user", ""),
        password=secret or "", connect_timeout=10)
    with psycopg.connect(conninfo) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(sql)
            cols = [{"name": d.name, "type": str(d.type_code)} for d in cur.description]
            rows = cur.fetchmany(max_rows)
    return cols, _rows(rows)


def _query_mysql(config, secret, sql, max_rows):
    import pymysql

    conn = pymysql.connect(host=config.get("host", "localhost"),
                           port=int(config.get("port", 3306)),
                           database=config.get("database", ""), user=config.get("user", ""),
                           password=secret or "", connect_timeout=10, read_timeout=60)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            cols = [{"name": d[0], "type": str(d[1])} for d in cur.description]
            rows = cur.fetchmany(max_rows)
    finally:
        conn.close()
    return cols, _rows(rows)


def _query_sqlite(config, _secret, sql, max_rows):
    conn = sqlite3.connect(config.get("path", ":memory:"), timeout=10)
    try:
        cur = conn.execute(sql)
        cols = [{"name": d[0], "type": ""} for d in cur.description or []]
        rows = cur.fetchmany(max_rows)
    finally:
        conn.close()
    return cols, _rows(rows)


def _query_sqlserver(config, secret, sql, max_rows):
    import pymssql

    conn = pymssql.connect(server=config.get("host", "localhost"),
                           port=str(config.get("port", 1433)),
                           database=config.get("database", ""), user=config.get("user", ""),
                           password=secret or "", login_timeout=10, timeout=60)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            cols = [{"name": d[0], "type": str(d[1])} for d in cur.description]
            rows = cur.fetchmany(max_rows)
    finally:
        conn.close()
    return cols, _rows(rows)


def _oracle_dsn(config) -> str:
    return "{}:{}/{}".format(config.get("host", "localhost"), int(config.get("port", 1521)),
                             config.get("database", config.get("service_name", "")))


def _query_oracle(config, secret, sql, max_rows):
    import oracledb

    conn = oracledb.connect(user=config.get("user", ""), password=secret or "",
                            dsn=_oracle_dsn(config), tcp_connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            cols = [{"name": d[0], "type": str(d[1])} for d in cur.description]
            rows = cur.fetchmany(max_rows)
    finally:
        conn.close()
    return cols, _rows(rows)


def _firebird_dsn(config) -> str:
    host, database = config.get("host"), config.get("database", "")
    return database if not host else f"{host}/{config.get('port', 3050)}:{database}"


def _query_firebird(config, secret, sql, max_rows):
    import firebird.driver as fbd

    conn = fbd.connect(database=_firebird_dsn(config), user=config.get("user", ""),
                       password=secret or "")
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            cols = [{"name": d[0], "type": str(d[1])} for d in cur.description]
            rows = cur.fetchmany(max_rows)
    finally:
        conn.close()
    return cols, _rows(rows)


def _bigquery_client(config, secret):
    from google.cloud import bigquery
    from google.oauth2 import service_account

    project = config.get("project", "")
    if secret:
        info = json.loads(secret)
        creds = service_account.Credentials.from_service_account_info(info)
        return bigquery.Client(project=project or info.get("project_id"), credentials=creds)
    return bigquery.Client(project=project or None)


def _query_bigquery(config, secret, sql, max_rows):
    client = _bigquery_client(config, secret)
    result = client.query(sql).result(max_results=max_rows)
    cols = [{"name": f.name, "type": f.field_type} for f in result.schema]
    return cols, [[v for v in row.values()] for row in result]


_ENGINES = {"postgresql": _query_postgresql, "mysql": _query_mysql, "sqlite": _query_sqlite,
            "bigquery": _query_bigquery, "sqlserver": _query_sqlserver, "oracle": _query_oracle,
            "firebird": _query_firebird}


async def execute_query(datasource: dict, sql: str, max_rows: int, timeout: float = 70.0):
    ensure_readonly(sql)
    engine = _ENGINES.get(datasource["kind"])
    if engine is None:
        raise ValueError(f"tipo de datasource não suportado: {datasource['kind']}")
    return await asyncio.wait_for(asyncio.to_thread(
        engine, datasource.get("config", {}), datasource.get("secret"), sql, max_rows), timeout)


# ------------------------------------------------------------------- mongo
def _mongo_client(config, secret):
    import pymongo

    uri = config.get("uri")
    if uri:
        return pymongo.MongoClient(uri, serverSelectionTimeoutMS=10_000)
    kwargs = {"serverSelectionTimeoutMS": 10_000, "connectTimeoutMS": 10_000}
    if config.get("user"):
        kwargs.update(username=config["user"], password=secret or "",
                      authSource=config.get("auth_source", "admin"))
    return pymongo.MongoClient(config.get("host", "localhost"), int(config.get("port", 27017)),
                               **kwargs)


def _mongo_value(v):
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, dict):
        return {k: _mongo_value(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_mongo_value(x) for x in v]
    return str(v)


def mongo_docs_to_rows(docs: list[dict]):
    order: list[str] = []
    seen = set()
    for d in docs:
        for k in d:
            if k not in seen:
                seen.add(k)
                order.append(k)
    return ([{"name": c, "type": "mixed"} for c in order],
            [[_mongo_value(d.get(c)) for c in order] for d in docs])


def _find_mongo(config, secret, collection, filter_, projection, limit):
    db = _mongo_client(config, secret)[config.get("database", "")]
    docs = list(db[collection].find(filter_ or {}, projection or None).limit(limit))
    return mongo_docs_to_rows(docs)


async def query_mongo(datasource, collection, filter_, projection, max_rows):
    """So `find`: nunca insert/update/delete/aggregate."""
    return await asyncio.wait_for(asyncio.to_thread(
        _find_mongo, datasource.get("config", {}), datasource.get("secret"), collection,
        filter_, projection, max_rows), 70.0)


def _list_mongo(config, secret):
    db = _mongo_client(config, secret)[config.get("database", "")]
    out = []
    for pos, nome in enumerate(sorted(db.list_collection_names())):
        if pos >= TABELAS_COM_COLUNAS:
            out.append({"table": nome, "columns": SEM_COLUNAS})
            continue
        campos: list[str] = []
        for d in db[nome].find({}, limit=20):
            campos += [k for k in d if k not in campos]
        out.append({"table": nome, "columns": [{"name": c, "type": "mixed"} for c in campos]
                    or "coleção vazia ou sem amostra disponível"})
    return out


# ---------------------------------------------------------------- catalogo
_NAMES_SQL = {
    "postgresql": "SELECT table_schema || '.' || table_name FROM information_schema.tables "
                  "WHERE table_schema NOT IN ('pg_catalog','information_schema') ORDER BY 1 LIMIT 500",
    "mysql": "SELECT CONCAT(table_schema,'.',table_name) FROM information_schema.tables "
             "WHERE table_schema = DATABASE() ORDER BY 1 LIMIT 500",
    "sqlserver": "SELECT TOP 500 table_schema + '.' + table_name FROM INFORMATION_SCHEMA.TABLES "
                 "WHERE table_schema NOT IN ('sys','INFORMATION_SCHEMA') ORDER BY 1",
}
_COLS_SQL = {
    "postgresql": "SELECT table_schema || '.' || table_name, column_name, data_type "
                  "FROM information_schema.columns WHERE table_schema NOT IN "
                  "('pg_catalog','information_schema') ORDER BY 1, ordinal_position LIMIT 500",
    "mysql": "SELECT CONCAT(table_schema,'.',table_name), column_name, data_type "
             "FROM information_schema.columns WHERE table_schema = DATABASE() "
             "ORDER BY 1, ordinal_position LIMIT 500",
    "sqlserver": "SELECT TOP 500 table_schema + '.' + table_name, column_name, data_type "
                 "FROM INFORMATION_SCHEMA.COLUMNS WHERE table_schema NOT IN "
                 "('sys','INFORMATION_SCHEMA') ORDER BY 1, ordinal_position",
}
# Nome de tabela e barato; coluna so nas primeiras N (nunca calar sobre o resto).
TABELAS_COM_COLUNAS = 50
SEM_COLUNAS = "colunas não listadas aqui — consulte INFORMATION_SCHEMA.COLUMNS desta tabela"


def _escolhidas(datasource: dict, nomes: list[str]) -> list[str]:
    chosen = datasource.get("config", {}).get("tables") or []
    if not chosen:
        return nomes
    want = {str(t).strip() for t in chosen}
    return [n for n in nomes if n in want or n.split(".")[-1] in want]


async def list_tables(datasource: dict, max_rows: int = 500) -> list[dict]:
    kind = datasource["kind"]
    if kind in _COLS_SQL:
        _, names = await execute_query(datasource, _NAMES_SQL[kind], max_rows)
        nomes = _escolhidas(datasource, [r[0] for r in names])
        _, rows = await execute_query(datasource, _COLS_SQL[kind], max_rows)
        tables: dict[str, list] = {}
        for t, c, dt in rows:
            tables.setdefault(t, []).append({"name": c, "type": dt})
        return [{"table": n, "columns": tables.get(n) or SEM_COLUNAS} for n in nomes]
    if kind == "sqlite":
        _, rows = await execute_query(
            datasource, "SELECT name FROM sqlite_master WHERE type = 'table' LIMIT 100", 100)
        out = []
        for (table,) in rows:
            if not re.fullmatch(r"[\w.$-]+", table):
                continue
            _, info = await execute_query(
                datasource, f"SELECT * FROM pragma_table_info('{table}')", 100)
            out.append({"table": table, "columns": [{"name": r[1], "type": r[2]} for r in info]})
        return out
    if kind == "mongodb":
        return await asyncio.to_thread(_list_mongo, datasource.get("config", {}),
                                       datasource.get("secret"))
    return [{"table": "*", "columns": "catálogo não disponível para este tipo de fonte; "
             "consulte o dicionário de dados do banco"}]


# ------------------------------------------------------------------ escrita
_WRITE_FIRST = re.compile(r"^\s*(insert|update|delete)\b", re.IGNORECASE)
_FORBIDDEN_WRITE = re.compile(
    r"\b(drop|alter|create|truncate|grant|revoke|vacuum|attach|pragma)\b", re.IGNORECASE)
_TARGET_TABLE = re.compile(
    r"^\s*(?:insert\s+into|update|delete\s+from)\s+([\"'`]?[\w.]+[\"'`]?)", re.IGNORECASE)


def validate_write(statement: str, allowed_tables: list[str]) -> str:
    """Guardrails de escrita. Devolve a tabela alvo ou levanta ValueError."""
    body = statement.strip().rstrip(";")
    if ";" in body:
        raise ValueError("apenas um statement por chamada")
    if not _WRITE_FIRST.match(body):
        raise ValueError("apenas INSERT, UPDATE ou DELETE são permitidos")
    if _FORBIDDEN_WRITE.search(body):
        raise ValueError("DDL/estruturais são proibidos nesta tool")
    first = body.split(None, 1)[0].lower()
    if first in ("update", "delete") and not re.search(r"\bwhere\b", body, re.IGNORECASE):
        raise ValueError(f"{first.upper()} sem WHERE é proibido")
    m = _TARGET_TABLE.match(body)
    if not m:
        raise ValueError("não foi possível identificar a tabela alvo")
    table = m.group(1).strip("\"'`").lower()
    normalized = {t.lower() for t in allowed_tables}
    bare = table.split(".")[-1]
    if table not in normalized and bare not in normalized:
        raise ValueError(f"tabela '{table}' não está na lista de escrita permitida deste template")
    return bare


def _connect_pg(config, secret):
    import psycopg

    return psycopg.connect(psycopg.conninfo.make_conninfo(
        host=config.get("host", "localhost"), port=int(config.get("port", 5432)),
        dbname=config.get("database", ""), user=config.get("user", ""),
        password=secret or "", connect_timeout=10))


def _write_postgresql(config, secret, sql):
    with _connect_pg(config, secret) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            count = cur.rowcount
            returned = [list(r) for r in cur.fetchall()] if cur.description is not None else None
        conn.commit()
    return count, returned


def _write_mysql(config, secret, sql):
    import pymysql

    conn = pymysql.connect(host=config.get("host", "localhost"),
                           port=int(config.get("port", 3306)),
                           database=config.get("database", ""), user=config.get("user", ""),
                           password=secret or "", connect_timeout=10)
    try:
        with conn.cursor() as cur:
            count = cur.execute(sql)
            returned = [[cur.lastrowid]] if cur.lastrowid else None
        conn.commit()
    finally:
        conn.close()
    return count, returned


def _write_sqlite(config, _secret, sql):
    conn = sqlite3.connect(config.get("path", ":memory:"), timeout=10)
    try:
        cur = conn.execute(sql)
        conn.commit()
        returned = None
        if cur.description is not None:
            returned = [list(r) for r in cur.fetchall()]
        elif cur.lastrowid:
            returned = [[cur.lastrowid]]
        return cur.rowcount, returned
    finally:
        conn.close()


def _write_dbapi(connect):
    def run(config, secret, sql):
        conn = connect(config, secret)
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                count = cur.rowcount
                returned = ([list(r) for r in cur.fetchall()]
                            if cur.description is not None else None)
            conn.commit()
        finally:
            conn.close()
        return count, returned
    return run


def _connect_mssql(config, secret):
    import pymssql

    return pymssql.connect(server=config.get("host", "localhost"),
                           port=str(config.get("port", 1433)),
                           database=config.get("database", ""), user=config.get("user", ""),
                           password=secret or "", login_timeout=10)


def _connect_oracle(config, secret):
    import oracledb

    return oracledb.connect(user=config.get("user", ""), password=secret or "",
                            dsn=_oracle_dsn(config))


def _connect_firebird(config, secret):
    import firebird.driver as fbd

    return fbd.connect(database=_firebird_dsn(config), user=config.get("user", ""),
                       password=secret or "")


def _write_bigquery(config, secret, sql):
    job = _bigquery_client(config, secret).query(sql)
    job.result()
    return job.num_dml_affected_rows or 0, None


_WRITE_ENGINES = {
    "postgresql": _write_postgresql, "mysql": _write_mysql, "sqlite": _write_sqlite,
    "bigquery": _write_bigquery, "sqlserver": _write_dbapi(_connect_mssql),
    "oracle": _write_dbapi(_connect_oracle), "firebird": _write_dbapi(_connect_firebird),
}


async def execute_write(datasource: dict, sql: str, allowed_tables: list[str]):
    table = validate_write(sql, allowed_tables)
    engine = _WRITE_ENGINES.get(datasource["kind"])
    if engine is None:
        raise ValueError(f"tipo de datasource não suportado: {datasource['kind']}")
    rows, returned = await asyncio.wait_for(asyncio.to_thread(
        engine, datasource.get("config", {}), datasource.get("secret"), sql), 70.0)
    return table, rows, returned


_PLACEHOLDER = re.compile(r"\{\{\s*returned:(\d+)(?::(\d+))?\s*\}\}")


def _substitute(statement: str, results: list[dict]) -> str:
    def repl(m: re.Match) -> str:
        idx = int(m.group(1))
        col = int(m.group(2)) if m.group(2) else 0
        if idx >= len(results) or not results[idx].get("returned"):
            raise ValueError(f"{{{{returned:{idx}}}}} não disponível (statement sem RETURNING)")
        value = results[idx]["returned"][0][col]
        if isinstance(value, (int, float)):
            return str(value)
        return "'" + str(value).replace("'", "''") + "'"

    return _PLACEHOLDER.sub(repl, statement)


def _run_transaction(kind, config, secret, statements):
    """Tudo numa transacao: qualquer falha desfaz o lote."""
    results: list[dict] = []
    if kind == "postgresql":
        with _connect_pg(config, secret) as conn:
            try:
                with conn.cursor() as cur:
                    for stmt in statements:
                        cur.execute(_substitute(stmt, results))
                        returned = [list(r) for r in cur.fetchall()] if cur.description else None
                        results.append({"affected_rows": cur.rowcount, "returned": returned})
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return results
    if kind == "sqlite":
        conn = sqlite3.connect(config.get("path", ":memory:"), timeout=10, isolation_level=None)
        try:
            conn.execute("BEGIN")
            for stmt in statements:
                cur = conn.execute(_substitute(stmt, results))
                if cur.description is not None:
                    returned = [list(r) for r in cur.fetchall()]
                elif cur.lastrowid:
                    returned = [[cur.lastrowid]]
                else:
                    returned = None
                results.append({"affected_rows": cur.rowcount, "returned": returned})
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return results
    if kind == "mysql":
        import pymysql

        conn = pymysql.connect(host=config.get("host", "localhost"),
                               port=int(config.get("port", 3306)),
                               database=config.get("database", ""), user=config.get("user", ""),
                               password=secret or "", connect_timeout=10)
        try:
            with conn.cursor() as cur:
                for stmt in statements:
                    count = cur.execute(_substitute(stmt, results))
                    results.append({"affected_rows": count,
                                    "returned": [[cur.lastrowid]] if cur.lastrowid else None})
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return results
    raise ValueError(f"transações não suportadas para datasource '{kind}'")


async def execute_transaction(datasource: dict, statements: list[str],
                              allowed_tables: list[str]) -> list[dict]:
    if not statements:
        raise ValueError("nenhum statement informado")
    if len(statements) > 50:
        raise ValueError("máximo de 50 statements por transação")
    tables = [validate_write(s, allowed_tables) for s in statements]
    rows = await asyncio.wait_for(asyncio.to_thread(
        _run_transaction, datasource["kind"], datasource.get("config", {}),
        datasource.get("secret"), statements), 70.0)
    return [{"table": t, **r} for t, r in zip(tables, rows, strict=True)]
