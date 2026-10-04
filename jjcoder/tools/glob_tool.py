"""File pattern matching."""

from pathlib import Path
from typing import ClassVar

from .base import Tool


class GlobTool(Tool):
    name = "glob"
    description = "Find files matching a glob pattern. Supports ** for recursive matching."
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern, e.g. '**/*.py'"},
            "path": {"type": "string", "description": "Directory to search in (default: cwd)"},
        },
        "required": ["pattern"],
    }

    def execute(self, pattern: str, path: str = ".") -> str:
        try:
            base = Path(path).expanduser().resolve()
            if not base.exists():
                return f"Error: {path} not found"
            if not base.is_dir():
                return f"Error: {path} is not a directory"
            hits = list(base.glob(pattern))
            hits.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
            shown = hits[:100]
            result = "\n".join(str(hit) for hit in shown)
            if len(hits) > 100:
                result += f"\n... ({len(hits)} matches, showing first 100)"
            return result or "No files matched."
        except Exception as e:  # noqa: BLE001
            return f"Error: {e}"
