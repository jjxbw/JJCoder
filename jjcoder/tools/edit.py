"""Safe search-and-replace file editing."""

import difflib
from pathlib import Path
from typing import ClassVar

from ..checkpoints import record as _record_checkpoint
from .base import Tool

_changed_files: set[str] = set()


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Edit a file by replacing an exact string match. "
        "old_string must appear exactly once in the file for safety. "
        "Include enough surrounding context to ensure uniqueness."
    )
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to the file to edit"},
            "old_string": {"type": "string", "description": "Exact text to find (must be unique in file)"},
            "new_string": {"type": "string", "description": "Replacement text"},
        },
        "required": ["file_path", "old_string", "new_string"],
    }

    def execute(self, file_path: str, old_string: str, new_string: str) -> str:
        from .base import FILE_MUTATION_LOCK

        try:
            with FILE_MUTATION_LOCK:
                p = Path(file_path).expanduser().resolve()
                if not p.exists():
                    return f"Error: {file_path} not found"
                try:
                    content = p.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    return f"Error: {file_path} is not a UTF-8 text file (edit_file only edits text files)"

                occurrences = content.count(old_string)
                if occurrences == 0:
                    preview = content[:500] + ("..." if len(content) > 500 else "")
                    return f"Error: old_string not found in {file_path}.\nFile starts with:\n{preview}"
                if occurrences > 1:
                    return (
                        f"Error: old_string appears {occurrences} times in {file_path}. "
                        "Include more surrounding lines to make it unique."
                    )

                new_content = content.replace(old_string, new_string, 1)
                _record_checkpoint(p)
                p.write_text(new_content, encoding="utf-8")
                _changed_files.add(str(p))

            return f"Edited {file_path}\n{_unified_diff(content, new_content, str(p))}"
        except Exception as e:  # noqa: BLE001
            return f"Error: {e}"


def _unified_diff(old: str, new: str, filename: str, context: int = 3) -> str:
    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        n=context,
    )
    result = "".join(diff)
    return result if len(result) <= 3000 else result[:2500] + "\n... (diff truncated)\n"
