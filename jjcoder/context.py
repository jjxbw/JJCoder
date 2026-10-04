"""Multi-layer context compression.
1.tool_snip: replace verbose tool results with truncated version
2.summarize: LLM-powered summary of old conversation context
3.collapse: hard truncate old context to fit within token limit"""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .llm import LLM

_CJK_RE = re.compile(r"[一-鿿㐀-䶿豈-﫿　-〿＀-･]")
_SYMBOL_RE = re.compile(r"[^\w\s]")

def _approx_tokens(text: str) -> int:
    cjk = len(_CJK_RE.findall(text))
    rest = len(text) - cjk
    dense = rest > 0 and len(_SYMBOL_RE.findall(text)) / len(text) > 0.25
    return math.ceil(cjk / 1.5) + int(rest / (2.8 if dense else 3.4))

def estimate_tokens(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        if m.get("content"):
            total += _approx_tokens(m["content"])
        if m.get("tool_calls"):
            total += _approx_tokens(str(m["tool_calls"]))
    return total

class ContextManager:
    def __init__(self, max_tokens: int = 128_000):
        self.max_tokens = max_tokens
        self._snip_at = int(max_tokens * 0.50)
        self._summarize_at = int(max_tokens * 0.75)
        self._collapse_at = int(max_tokens * 0.90)

    def maybe_compress(self, messages: list[dict], llm: LLM | None = None) -> bool:
        current = estimate_tokens(messages)
        compressed = False

        if current > self._snip_at and self._snip_tool_outputs(messages):
            compressed = True
            current = estimate_tokens(messages)

        if current > self._summarize_at and len(messages) > 10 and self._summarize_old(messages, llm, keep_recent=8):
            compressed = True
            current = estimate_tokens(messages)

        if current > self._collapse_at and len(messages) > 4:
            self._hard_collapse(messages, llm)
            compressed = True

        return compressed

    @staticmethod
    def _snip_tool_outputs(messages: list[dict]) -> bool:
        changed = False
        for m in messages:
            if m.get("role") != "tool":
                continue
            content = m.get("content", "")
            if len(content) <= 1500:
                continue
            lines = content.splitlines()
            if len(lines) <= 6:
                continue
            snipped = (
                "\n".join
                + f"\n...({len(lines)}lines, snipped to save context)...\n"
                + "\n".join(lines[-3:])
            )
            m["content"] = snipped
            changed = True
        return changed

    @staticmethod
    def _safe_split(messages: list[dict], keep_recent: int) -> int:
        split = max(0, len(messages) - keep_recent)
        while split > 0 and messages[split].get("role") == "tool":
            split -= 1
        return split

    def _summarize_old(self, messages: list[dict], llm: LLM | None, keep_recent: int = 8) -> bool:
        if len(messages) <= keep_recent:
            return False
        split = self._safe_split(messages, keep_recent)
        old = messages[:split]
        tail = messages[split:]

        summary = self._get_summary(old, llm)

        messages.clear()
        messages.append({
            "role": "user", 
            "content": f"[Context compressed - conversation summary]\n{summary}",
        })
        messages.append({
            "role": "assistant",
            "content": "Got it, I have the context from our earlier conversation.",
        })
        messages.extend(tail)
        return True

    def _hard_collapse(self, messages: list[dict], llm: LLM | None):
        split = self._safe_split(messages, 4 if len(messages) > 4 else 2)
        tail = messages[split:]
        summary = self._get_summary(messages[:split], llm)

        messages.clear()
        messages.append({
            "role": "user",
            "content": f"[Hard context reset]\n{summary}",
        })
        messages.append({
            "role": "assistant",
            "content": "Context restored. Continuing from where we left off.",
        })
        messages.extend(tail)

    def _get_summary(self, messages: list[dict], llm: LLM | None) -> str:
        flat = self._flatten(messages)

        if llm:
            try:
                resp = llm.chat(
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Compress this conversation into a brief summary. "
                                "preserve: file paths edited, key decisions made, "
                                "errors encountered, current task state. "
                                "Drop: verbose command output, code listings, "
                                "redundant back-and-forth."
                            ),
                        },
                        {
                            "role": "user",
                            "content": flat[:15000]
                        },
                    ],
                )
                return resp.content
            except Exception:
                pass

        return self._extract_key_info(messages)

    @staticmethod
    def _flatten(messages: list[dict]) -> str:
        parts = []
        for m in messages:
            role = m.get("role", "?")
            text = m.get("content", "") or ""
            if text:
                parts.append(f"[{role}] {text[:400]}")
        return "\n".join(parts)

    @staticmethod
    def _extract_key_info(messages: list[dict]) -> str:
        import re
        file_seen = set()
        errors = []

        for m in messages:
            text = m.get("content", "") or ""
            for match in re.finditer(r'[\w./\-]+\.\w{1,5}', text):
                file_seen.add(match.group())
            for line in text.splitlines():
                if "error" in line.lower():
                    errors.append(line.strip()[:150])

        parts = []
        if file_seen:
            parts.append(f"Files touched: {', '.join(sorted(files_seen)[:20])}")
        if errors:
            parts.append(f"Errors seen: {'; '.join(errors[:5])}")
        return "\n".join(parts) or "(no extractable context)"


        
       