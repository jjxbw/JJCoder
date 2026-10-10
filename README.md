# JJCoder

基于大语言模型的命令行 Coding Agent。支持文件操作、项目分析、Web 搜索、MCP 外部工具和受控的数据库/业务服务调用。

## 内置 MCP Server

JJCoder 自带两个可通过 stdio 启动的 MCP Server：

```json
{
  "mcpServers": {
    "sql": {
      "command": "python",
      "args": ["-m", "jjcoder.mcp_servers.sql"],
      "env": {
        "JJCODER_SQL_DATABASES": "{\"analytics\":{\"type\":\"postgres\",\"url\":\"postgresql://user:password@db.example.com:5432/analytics\"}}"
      }
    },
    "business": {
      "command": "python",
      "args": ["-m", "jjcoder.mcp_servers.http"],
      "env": {
        "JJCODER_HTTP_ALLOWED_HOSTS": "api.example.com",
        "JJCODER_HTTP_DEFAULT_HEADERS": "{\"Authorization\":\"Bearer ...\"}"
      }
    }
  }
}
```

保存到 `~/.jjcoder/mcp.json` 后，启动 JJCoder 即可加载工具。

SQL Server 提供 `sql_connections`、`sql_list_tables`、`sql_describe_table`、`sql_query` 和 `sql_execute`，支持 SQLite、PostgreSQL 和 MySQL。PostgreSQL/MySQL 驱动通过 `pip install 'jjcoder[sql]'` 安装；写操作默认关闭，需要在 MCP Server 的环境中显式设置 `JJCODER_SQL_ALLOW_WRITE=1`。

HTTP Server 提供 `http_request`。目标主机必须配置在 `JJCODER_HTTP_ALLOWED_HOSTS` 中，敏感认证头应由服务端环境配置，不应交给模型。

MCP 工具的凭据和连接配置保留在服务端，JJCoder 只接收工具 schema 和执行结果。生产环境建议使用只读数据库账号、最小权限的业务账号、超时/审计和独立的密钥管理系统。
