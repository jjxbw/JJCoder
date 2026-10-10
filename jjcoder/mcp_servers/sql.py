"""Database MCP server for SQLite, PostgreSQL, and MySQL.

Database credentials and URLs are configured on the MCP server, never by the
model. Example::

    JJCODER_SQL_DATABASES='{"local":"/tmp/app.db","analytics":{"type":"postgres","url":"postgresql://..."},"legacy":{"type":"mysql","url":"mysql://..."}}'

Install optional drivers with ``pip install 'jjcoder[sql]'``.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, unquote, urlparse

from .common import MCPServer, tool

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_READ_SQL = re.compile(r"^(?:select|with|explain|pragma)\b", re.IGNORECASE)
_WRITE_SQL = re.compile(r"^(?:insert|update|delete|replace)\b", re.IGNORECASE)
_DDL_SQL = re.compile(r"^(?:create|alter|drop|vacuum|reindex|attach|detach)\b", re.IGNORECASE)
_MUTATING_WORD = re.compile(r"\b(?:insert|update|delete|replace|drop|alter|create)\b", re.IGNORECASE)


def _connections() -> dict[str, dict[str, str]]:
    raw = os.getenv("JJCODER_SQL_DATABASES")
    if not raw:
        return {"default": {"type": "sqlite", "target": os.getenv("JJCODER_SQLITE_PATH", ":memory:")}}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("JJCODER_SQL_DATABASES must be a JSON object")

    result: dict[str, dict[str, str]] = {}
    for name, value in data.items():
        if isinstance(value, str):
            result[str(name)] = {"type": "sqlite", "target": value}
            continue
        if not isinstance(value, dict):
            raise ValueError(f"connection {name!r} must be a path or object")
        kind = str(value.get("type", "sqlite")).lower()
        if kind == "postgresql":
            kind = "postgres"
        if kind not in {"sqlite", "postgres", "mysql"}:
            raise ValueError(f"unsupported database type for {name!r}: {kind}")
        target = value.get("path") if kind == "sqlite" else (value.get("url") or value.get("dsn"))
        if not target:
            raise ValueError(f"connection {name!r} is missing path/url/dsn")
        result[str(name)] = {"type": kind, "target": str(target)}
    return result


def _spec(database: str) -> dict[str, str]:
    databases = _connections()
    if database not in databases:
        raise ValueError(f"unknown database {database!r}; available: {', '.join(sorted(databases))}")
    return databases[database]


@contextmanager
def _open(database: str) -> Iterator[tuple[str, Any]]:
    spec = _spec(database)
    kind, target = spec["type"], spec["target"]
    connection = None
    try:
        if kind == "sqlite":
            path = target if target == ":memory:" else str(Path(target).expanduser())
            connection = sqlite3.connect(path, timeout=10)
            connection.row_factory = sqlite3.Row
        elif kind == "postgres":
            try:
                import psycopg
            except ImportError as exc:
                raise RuntimeError("PostgreSQL support requires: pip install 'jjcoder[sql]'") from exc
            connection = psycopg.connect(target, connect_timeout=10)
        else:
            try:
                import pymysql
            except ImportError as exc:
                raise RuntimeError("MySQL support requires: pip install 'jjcoder[sql]'") from exc
            connection = _connect_mysql(pymysql, target)
        yield kind, connection
    finally:
        if connection is not None:
            connection.close()


def _connect_mysql(pymysql, target: str):
    parsed = urlparse(target)
    if parsed.scheme not in {"mysql", "mysql+pymysql"} or not parsed.hostname:
        raise ValueError("MySQL URL must look like mysql://user:password@host:3306/database")
    query = parse_qs(parsed.query)
    return pymysql.connect(
        host=parsed.hostname,
        port=parsed.port or 3306,
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
        database=unquote(parsed.path.lstrip("/")),
        connect_timeout=10,
        charset=query.get("charset", ["utf8mb4"])[0],
        autocommit=False,
    )


def _params(value):
    if value is None:
        return ()
    if not isinstance(value, (dict, list, tuple)):
        raise ValueError("params must be an object or array")
    return value


def _adapt_sql(statement: str, kind: str) -> str:
    if kind == "sqlite":
        return statement
    statement = re.sub(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)", r"%(\1)s", statement)
    return statement.replace("?", "%s")


def _fetch_json(cursor, limit: int) -> str:
    rows = cursor.fetchmany(max(1, min(int(limit), 500)))
    columns = []
    for column in cursor.description or []:
        name = getattr(column, "name", None)
        columns.append(name if name is not None else column[0])
    payload = []
    for row in rows:
        if isinstance(row, dict):
            payload.append({column: row.get(column) for column in columns})
        else:
            payload.append({column: row[index] for index, column in enumerate(columns)})
    return json.dumps({"columns": columns, "rows": payload, "row_count": len(payload)}, ensure_ascii=False, default=str)


def _execute_query(connection, kind: str, statement: str, params, limit: int) -> str:
    cursor = connection.cursor()
    cursor.execute(_adapt_sql(statement, kind), _params(params))
    try:
        return _fetch_json(cursor, limit)
    finally:
        cursor.close()


def list_connections() -> str:
    return json.dumps(
        {"connections": [{"name": name, "type": spec["type"]} for name, spec in sorted(_connections().items())]},
        ensure_ascii=False,
    )


def list_tables(database: str = "default") -> str:
    with _open(database) as (kind, connection):
        cursor = connection.cursor()
        if kind == "sqlite":
            cursor.execute("SELECT name, type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name")
            rows = [{"name": row[0], "type": row[1]} for row in cursor.fetchall()]
        elif kind == "postgres":
            cursor.execute("SELECT table_schema, table_name, table_type FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog', 'information_schema') ORDER BY table_schema, table_name")
            rows = [{"schema": row[0], "name": row[1], "type": row[2]} for row in cursor.fetchall()]
        else:
            cursor.execute("SELECT table_schema, table_name, table_type FROM information_schema.tables WHERE table_schema = DATABASE() ORDER BY table_name")
            rows = [{"schema": row[0], "name": row[1], "type": row[2]} for row in cursor.fetchall()]
        cursor.close()
    return json.dumps({"database": database, "tables": rows}, ensure_ascii=False)


def describe_table(table: str, database: str = "default") -> str:
    if not _IDENTIFIER.fullmatch(table):
        raise ValueError("table must be a simple identifier")
    with _open(database) as (kind, connection):
        cursor = connection.cursor()
        if kind == "sqlite":
            cursor.execute(f'PRAGMA table_info("{table}")')
            rows = [{"name": row[1], "type": row[2], "nullable": not bool(row[3]), "default": row[4], "primary_key": bool(row[5])} for row in cursor.fetchall()]
        elif kind == "postgres":
            cursor.execute("SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = %s ORDER BY ordinal_position", (table,))
            rows = [{"name": row[0], "type": row[1], "nullable": row[2] == "YES", "default": row[3]} for row in cursor.fetchall()]
        else:
            cursor.execute(f"DESCRIBE `{table}`")
            rows = [{"name": row[0], "type": row[1], "nullable": row[2] == "YES", "default": row[4], "primary_key": row[3] == "PRI"} for row in cursor.fetchall()]
        cursor.close()
    if not rows:
        raise ValueError(f"table {table!r} not found")
    return json.dumps({"database": database, "table": table, "columns": rows}, ensure_ascii=False, default=str)


def _validate_read_statement(statement: str) -> None:
    if not _READ_SQL.match(statement):
        raise ValueError("sql_query only accepts SELECT, WITH, EXPLAIN, or PRAGMA statements")
    if ";" in statement:
        raise ValueError("multiple SQL statements are not allowed")
    if statement.lower().startswith("with") and _MUTATING_WORD.search(statement):
        raise ValueError("mutating CTEs are not allowed in sql_query")


def query(sql: str, params=None, database: str = "default", limit: int = 100) -> str:
    statement = sql.strip().rstrip(";").strip()
    _validate_read_statement(statement)
    with _open(database) as (kind, connection):
        return _execute_query(connection, kind, statement, params, limit)


def execute(sql: str, params=None, database: str = "default") -> str:
    if os.getenv("JJCODER_SQL_ALLOW_WRITE", "0").lower() not in {"1", "true", "yes"}:
        raise ValueError("SQL writes are disabled; set JJCODER_SQL_ALLOW_WRITE=1 on the MCP server")
    statement = sql.strip().rstrip(";").strip()
    if ";" in statement:
        raise ValueError("multiple SQL statements are not allowed")
    if not (_WRITE_SQL.match(statement) or (_DDL_SQL.match(statement) and os.getenv("JJCODER_SQL_ALLOW_DDL") == "1")):
        raise ValueError("only DML is enabled; DDL requires JJCODER_SQL_ALLOW_DDL=1")
    with _open(database) as (kind, connection):
        cursor = connection.cursor()
        cursor.execute(_adapt_sql(statement, kind), _params(params))
        connection.commit()
        rowcount = cursor.rowcount
        cursor.close()
    return json.dumps({"database": database, "rowcount": rowcount, "committed": True})


def build_server() -> MCPServer:
    return MCPServer(
        "jjcoder-sql",
        [
            tool("sql_connections", "List configured database connections without exposing credentials.", {"type": "object", "properties": {}}, list_connections, True),
            tool("sql_list_tables", "List tables in a configured database.", {"type": "object", "properties": {"database": {"type": "string"}}}, list_tables, True),
            tool("sql_describe_table", "Describe columns and primary-key metadata for a table.", {"type": "object", "properties": {"database": {"type": "string"}, "table": {"type": "string"}}, "required": ["table"]}, describe_table, True),
            tool("sql_query", "Run a parameterized read-only SQL query with a bounded result set.", {"type": "object", "properties": {"database": {"type": "string"}, "sql": {"type": "string"}, "params": {"type": ["object", "array", "null"]}, "limit": {"type": "integer"}}, "required": ["sql"]}, query, True),
            tool("sql_execute", "Run one parameterized write statement. Requires server-side write enablement and user approval in JJCoder.", {"type": "object", "properties": {"database": {"type": "string"}, "sql": {"type": "string"}, "params": {"type": ["object", "array", "null"]}}, "required": ["sql"]}, execute),
        ],
    )


if __name__ == "__main__":
    build_server().run()
