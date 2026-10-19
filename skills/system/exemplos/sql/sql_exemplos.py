#!/usr/bin/env python3
"""Leitura e escrita com transacao em sqlite, PostgreSQL e MySQL. Rodar o arquivo faz uma demo em sqlite."""
import os
import sqlite3
import tempfile
from pathlib import Path


# ---------------------------------------------------------------- sqlite (stdlib)
def sqlite_ler(caminho: str, sql: str, params: tuple = ()) -> list[dict]:
    con = sqlite3.connect(caminho)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def sqlite_escrever(caminho: str, comandos: list[tuple[str, tuple]]) -> int:
    """Varios comandos numa transacao; devolve linhas afetadas. Erro -> nada e gravado."""
    con = sqlite3.connect(caminho)
    try:
        with con:  # commit se terminar bem, rollback se levantar excecao
            total = 0
            for sql, params in comandos:
                total += max(con.execute(sql, params).rowcount, 0)
            return total
    finally:
        con.close()


# ---------------------------------------------------------------- PostgreSQL (psycopg 3)
def pg_ler(sql: str, params: tuple = ()) -> list[dict]:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row, connect_timeout=15) as con:
        con.read_only = True
        return con.execute(sql, params).fetchall()


def pg_escrever(comandos: list[tuple[str, tuple]]) -> int:
    import psycopg

    total = 0
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=15) as con:  # commit/rollback no with
        for sql, params in comandos:
            total += con.execute(sql, params).rowcount
    return total


# ---------------------------------------------------------------- MySQL (pymysql)
def _mysql():
    import pymysql

    pw = os.environ["DB_PASS"]
    return pymysql.connect(host=os.environ["DB_HOST"], user=os.environ["DB_USER"],
                           password=pw, database=os.environ["DB_NAME"],
                           cursorclass=pymysql.cursors.DictCursor, connect_timeout=15)


def mysql_ler(sql: str, params: tuple = ()) -> list[dict]:
    con = _mysql()
    try:
        with con.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())
    finally:
        con.close()


def mysql_escrever(comandos: list[tuple[str, tuple]]) -> int:
    con = _mysql()
    try:
        total = 0
        with con.cursor() as cur:
            for sql, params in comandos:
                total += cur.execute(sql, params)
        con.commit()
        return total
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def demo() -> None:
    caminho = str(Path(tempfile.mkdtemp()) / "demo.db")
    sqlite_escrever(caminho, [("CREATE TABLE pedidos (id INTEGER PRIMARY KEY, cliente TEXT, valor REAL)", ()),
                              ("INSERT INTO pedidos (cliente, valor) VALUES (?, ?)", ("Ana", 10.5)),
                              ("INSERT INTO pedidos (cliente, valor) VALUES (?, ?)", ("Bruno", 20.0))])
    print(sqlite_ler(caminho, "SELECT cliente, valor FROM pedidos WHERE valor > ?", (15,)))
    try:  # o segundo comando falha: o primeiro e desfeito
        sqlite_escrever(caminho, [("UPDATE pedidos SET valor = 0", ()),
                                  ("UPDATE tabela_que_nao_existe SET x = 1", ())])
    except sqlite3.OperationalError:
        pass
    print("apos rollback:", sqlite_ler(caminho, "SELECT sum(valor) AS total FROM pedidos"))


if __name__ == "__main__":
    demo()
