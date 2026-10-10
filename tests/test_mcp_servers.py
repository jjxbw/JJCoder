import json
import sqlite3

from jjcoder.mcp_servers import sql
from jjcoder.mcp_servers.common import MCPServer
from jjcoder.mcp import load_mcp_tools


def test_sql_server_can_list_describe_query_and_write(monkeypatch, tmp_path):
    database = tmp_path / "app.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    connection.execute("INSERT INTO users (name) VALUES ('Ada')")
    connection.commit()
    connection.close()
    monkeypatch.setenv("JJCODER_SQL_DATABASES", json.dumps({"app": str(database)}))
    monkeypatch.setenv("JJCODER_SQL_ALLOW_WRITE", "1")

    assert "users" in sql.list_tables("app")
    assert "name" in sql.describe_table("users", "app")
    assert "Ada" in sql.query("SELECT * FROM users", database="app")
    assert '"rowcount": 1' in sql.execute("UPDATE users SET name = :name", {"name": "Grace"}, "app")


def test_sql_server_rejects_unsafe_queries(monkeypatch):
    monkeypatch.setenv("JJCODER_SQL_DATABASES", json.dumps({"default": ":memory:"}))
    try:
        sql.query("DELETE FROM users")
    except ValueError as exc:
        assert "only accepts" in str(exc)
    else:
        raise AssertionError("write statement was accepted by sql_query")


def test_mcp_server_handles_initialize_and_tool_listing():
    server = MCPServer("test", [])
    initialized = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})

    assert initialized["result"]["serverInfo"]["name"] == "test"
    assert listed["result"]["tools"] == []


def test_jjcoder_mcp_client_discovers_builtin_sql_server(tmp_path):
    database = tmp_path / "app.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE items (id INTEGER, name TEXT)")
    connection.execute("INSERT INTO items VALUES (1, 'one')")
    connection.commit()
    connection.close()
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "sql": {
                        "command": "python",
                        "args": ["-m", "jjcoder.mcp_servers.sql"],
                        "env": {"JJCODER_SQLITE_PATH": str(database)},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    tools = load_mcp_tools(config)
    query = next(item for item in tools if item.name == "mcp__sql__sql_query")

    assert query.read_only
    assert "one" in query.execute(sql="SELECT * FROM items")
