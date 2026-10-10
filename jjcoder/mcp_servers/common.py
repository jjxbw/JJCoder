"""Minimal stdio MCP JSON-RPC server used by the built-in adapters."""

from __future__ import annotations

import json
import sys
import traceback
from collections.abc import Callable


class MCPServer:
    def __init__(self, name: str, tools: list[dict]):
        self.name = name
        self.tools = {tool["name"]: tool for tool in tools}

    def run(self) -> None:
        for line in sys.stdin:
            try:
                message = json.loads(line)
                response = self.handle(message)
                if response is not None:
                    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                    sys.stdout.flush()
            except Exception as exc:  # noqa: BLE001
                _log(f"request failed: {exc}")

    def handle(self, message: dict) -> dict | None:
        method = message.get("method")
        request_id = message.get("id")
        if request_id is None:
            return None
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": message.get("params", {}).get("protocolVersion", "2025-06-18"),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": self.name, "version": "0.1.0"},
                }
            elif method == "tools/list":
                result = {
                    "tools": [
                        {
                            "name": tool["name"],
                            "description": tool["description"],
                            "inputSchema": tool["inputSchema"],
                            **({"annotations": tool["annotations"]} if tool.get("annotations") else {}),
                        }
                        for tool in self.tools.values()
                    ]
                }
            elif method == "tools/call":
                params = message.get("params") or {}
                tool = self.tools.get(params.get("name"))
                if tool is None:
                    raise ValueError(f"unknown tool: {params.get('name')}")
                value = tool["handler"](**(params.get("arguments") or {}))
                result = {"content": [{"type": "text", "text": str(value)}]}
            else:
                raise ValueError(f"unsupported method: {method}")
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as exc:  # noqa: BLE001
            _log(f"{method} failed: {exc}")
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": str(exc)},
            }


def tool(name: str, description: str, input_schema: dict, handler: Callable, read_only: bool = False) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": input_schema,
        "handler": handler,
        "annotations": {"readOnlyHint": True} if read_only else {"readOnlyHint": False},
    }


def _log(message: str) -> None:
    print(f"[jjcoder-mcp] {message}", file=sys.stderr)
