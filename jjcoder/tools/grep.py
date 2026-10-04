"""Content search with regular expressions."""

import re
from pathlib import Path
from typing import ClassVar

from .base import Tool

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox", "dist", "build"}


class GrepTool(Tool):
    name = "grep"
    description = "Search file contents with regex. Returns matching lines with file path and line number."
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search for"},
            "path": {"type": "string", "description": "File or directory to search (default: cwd)"},
            "include": {"type": "string", "description": "Only search files matching this glob"},
        },
        "required": ["pattern"],
    }

    def execute(self, pattern: str, path: str = ".", include: str | None = None) -> str:
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return f"Invalid regex: {e}"

        base = Path(path).expanduser().resolve()
        if not base.exists():
            return f"Error: {path} not found"
        files, scan_truncated = ([base], False) if base.is_file() else self._walk(base, include)
        scan_limit_msg = "... (5000 file scan limit reached; results may be incomplete)"
        matches = []
        for fp in files:
            try:
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    matches.append(f"{fp}:{lineno}: {line.rstrip()}")
                    if len(matches) >= 200:
                        matches.append("... (200 match limit reached)")
                        if scan_truncated:
                            matches.append(scan_limit_msg)
                        return "\n".join(matches)

        if matches:
            if scan_truncated:
                matches.append(scan_limit_msg)
            return "\n".join(matches)
        return "No matches found." if not scan_truncated else f"No matches found in scanned files.\n{scan_limit_msg}"

    @staticmethod
    def _walk(root: Path, include: str | None) -> tuple[list[Path], bool]:
        results = []
        for item in root.rglob(include or "*"):
            if any(part in _SKIP_DIRS for part in item.relative_to(root).parts):
                continue
            if item.is_file():
                results.append(item)
            if len(results) >= 5000:
                return results, True
        return results, False
