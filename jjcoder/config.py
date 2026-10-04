import os
from dataclasses import dataclass
from pathlib import Path

def _load_dotenv():
    from dotenv import load_dotenv

    env_path = Path(".env")
    if not env_path.exists():
        cur = Path.cwd()
        home = Path.home()
        while cur != home and cur != cur.parent:
            candidate = cur / ".env"
            if candidate.exists():
                env_path = candidate
                break
            cur = cur.parent
    load_dotenv(env_path, override=False)

@dataclass
class Config:
    model: str = "gpt-5.5"
    api_key: str = ""
    base_url: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.0
    max_context_tokens: int = 128_000
    provider: str = "openai"

    @classmethod
    def from_env(cls) -> "Config":
        _load_dotenv()
        api_key = (
            os.getenv("JJCODER_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or ""
        )
        return cls(
            model=os.getenv("JJCODER_MODEL", "gpt-5.5"),
            api_key=api_key,
            base_url=os.getenv("OPENAI_BASE_URL") or os.getenv("JJCODER_BASE_URL"),
            max_tokens=int(os.getenv("JJCODER_MAX_TOKENS", "4096")),
            temperature=float(os.getenv("JJCODER_TEMPERATURE", "0")),
            max_context_tokens=int(os.getenv("JJCODER_MAX_CONTEXT", "128000")),
            provider=os.getenv("JJCODER_PROVIDER", "openai"),
        )
