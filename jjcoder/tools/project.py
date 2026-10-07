"""Project inspection and Mermaid diagram generation."""

from __future__ import annotations

import ast
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import ClassVar

from .base import Tool

_SKIP_DIRS = {".git", ".jjcoder", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", ".tox"}
_LANGUAGES = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".java": "Java", ".go": "Go", ".rs": "Rust", ".c": "C", ".cpp": "C++", ".h": "C/C++",
    ".cs": "C#", ".rb": "Ruby", ".php": "PHP", ".swift": "Swift", ".sql": "SQL",
}


def _root(path: str) -> Path:
    root = Path(path).expanduser().resolve()
    if not root.exists():
        raise ValueError(f"{path} not found")
    if not root.is_dir():
        raise ValueError(f"{path} is not a directory")
    return root


def _files(root: Path, limit: int = 2000) -> tuple[list[Path], bool]:
    found: list[Path] = []
    for item in root.rglob("*"):
        relative = item.relative_to(root)
        if any(part in _SKIP_DIRS for part in relative.parts):
            continue
        if item.is_file():
            found.append(item)
            if len(found) >= limit:
                return sorted(found), True
    return sorted(found), False


def _python_imports(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, SyntaxError, UnicodeError):
        return []
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module.split(".")[0])
    return sorted(set(imports))


def _scan(path: str) -> dict:
    root = _root(path)
    files, truncated = _files(root)
    languages = Counter(_LANGUAGES.get(item.suffix.lower(), "Other") for item in files)
    modules = []
    for item in files:
        relative = item.relative_to(root).as_posix()
        entry = {
            "path": relative,
            "language": _LANGUAGES.get(item.suffix.lower(), "Other"),
            "lines": _line_count(item),
        }
        if item.suffix == ".py":
            imports = _python_imports(item)
            if imports:
                entry["imports"] = imports[:20]
        modules.append(entry)
    entry_names = {"main.py", "__main__.py", "app.py", "server.py", "index.js", "index.ts", "manage.py"}
    entry_points = [m["path"] for m in modules if Path(m["path"]).name in entry_names]
    config_names = {"pyproject.toml", "package.json", "requirements.txt", "Cargo.toml", "go.mod", "pom.xml", "Dockerfile", ".env.example"}
    configs = [m["path"] for m in modules if Path(m["path"]).name in config_names]
    return {
        "root": str(root),
        "project": root.name,
        "file_count": len(files),
        "truncated": truncated,
        "languages": dict(languages.most_common()),
        "entry_points": sorted(entry_points),
        "configuration_files": sorted(configs),
        "modules": modules,
    }


def _line_count(path: Path) -> int:
    try:
        return sum(1 for _ in path.open("r", encoding="utf-8", errors="ignore"))
    except OSError:
        return 0


class ProjectScanTool(Tool):
    name = "project_scan"
    description = "Inspect a project directory and return structured JSON describing files, languages, entry points, and Python imports."
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Project directory, default cwd"}},
        "required": [],
    }

    def execute(self, path: str = ".") -> str:
        try:
            return json.dumps(_scan(path), ensure_ascii=False, indent=2)
        except Exception as e:  # noqa: BLE001
            return f"Error scanning project: {e}"


class ProjectDiagramTool(Tool):
    name = "project_diagram"
    description = "Analyze a project and generate an editable Mermaid mindmap or architecture diagram."
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project directory, default cwd"},
            "diagram_type": {"type": "string", "enum": ["mindmap", "architecture"], "description": "Diagram style"},
        },
        "required": [],
    }

    def execute(self, path: str = ".", diagram_type: str = "architecture") -> str:
        try:
            data = _scan(path)
            if diagram_type == "mindmap":
                return _mindmap(data)
            if diagram_type == "architecture":
                return _architecture(data)
            return "Error: diagram_type must be 'mindmap' or 'architecture'"
        except Exception as e:  # noqa: BLE001
            return f"Error generating project diagram: {e}"


def _label(value: str) -> str:
    return re.sub(r'["\[\]{}()]', "", value).strip() or "module"


def _mindmap(data: dict) -> str:
    lines = ["```mermaid", "mindmap", f"  root(({_label(data['project'])}))"]
    groups: dict[str, list[str]] = defaultdict(list)
    for module in data["modules"][:150]:
        parts = Path(module["path"]).parts
        group = parts[0] if len(parts) > 1 else "root files"
        groups[group].append("/".join(parts[1:]) if len(parts) > 1 else parts[0])
    for group, items in sorted(groups.items()):
        lines.append(f"    {_label(group)}")
        for item in items[:30]:
            lines.append(f"      {_label(item)}")
    if data["truncated"]:
        lines.append("    ... file scan truncated")
    lines.append("```")
    return "\n".join(lines)


def _architecture(data: dict) -> str:
    lines = ["```mermaid", "flowchart LR", f"  ROOT[\"{_label(data['project'])}\"]"]
    nodes: dict[str, str] = {}
    for module in data["modules"][:100]:
        path = module["path"]
        node = "N" + str(len(nodes))
        nodes[path] = node
        lines.append(f"  {node}[\"{_label(path)}\"]")
        top = Path(path).parts[0] if len(Path(path).parts) > 1 else "root"
        group = "G" + re.sub(r"\W", "", top)[:20]
        if not any(line.startswith(f"  {group}[") for line in lines):
            lines.append(f"  {group}[[\"{_label(top)}\"]]")
        lines.append(f"  {group} --> {node}")
    for module in data["modules"][:100]:
        source = nodes.get(module["path"])
        for dependency in module.get("imports", []):
            target = next((node for path, node in nodes.items() if Path(path).stem == dependency), None)
            if source and target and source != target:
                lines.append(f"  {source} -.-> {target}")
    lines.append("```")
    return "\n".join(lines)
