"""LLM provider layer"""

import json
import time
from dataclasses import dataclass, field

from openai import APIConnectionError, APIError, APITimeoutError, BadRequestError, OpenAI, RateLimitError

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict

@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def message(self) -> dict:
        msg: dict = {"role": "assistant", "content": self.content or None}
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in self.tool_calls
            ]
        return msg

_PRICING = {
    # OpenAI - current flagships
    "gpt-5.5": (5, 30),
    "gpt-5.4": (2.5, 15),
    "gpt-5.4-mini": (0.75, 4.5),
    "gpt-5.4-nano": (0.2, 1.25),
    "o4-mini": (1.1, 4.4),
    # OpenAI - previous gen (still widely used)
    "gpt-4.1": (2, 8),
    "gpt-4.1-mini": (0.4, 1.6),
    "gpt-4.1-nano": (0.1, 0.4),
    "gpt-4o": (2.5, 10),
    "gpt-4o-mini": (0.15, 0.6),
    # DeepSeek
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    # Anthropic Claude
    "claude-opus-4-6": (5, 25),
    "claude-sonnet-4-6": (3, 15),
    "claude-haiku-4-5": (1, 5),
    # Alibaba Qwen
    "qwen3-max": (0.78, 3.9),
    "qwen3-plus": (0.26, 0.78),
    "qwen-max": (0.78, 3.9),
    # Moonshot Kimi
    "kimi-k3": (3, 15),
    "kimi-k2.5": (0.6, 3),
}

def _adapt_rejected_param(params: dict, exc: BadRequestError) -> bool:
    message = str(exc).lower()
    if "'max_tokens'" in message and "max_tokens" in params:
        params["max_completion_tokens"] = params.pop("max_tokens")
        return True
    if "'temperature'" in message and "temperature" in params:
        params.pop("temperature")
        return True
    return False

class LLM:
    def __init__(
            self,
            model: str,
            api_key: str,
            base_url: str | None = None,
            **kwargs,
    ):
        self.model = model
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.extra = kwargs
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    @property
    def estimated_cost(self) -> float | None:
        pricing = _PRICING.get(self.model)
        if not pricing:
            return None
        input_rate, output_rate = pricing
        return (
            self.total_prompt_tokens * input_rate / 1000
            + self.total_completion_tokens * output_rate / 1000
        )

    def chat(
            self,
            messages: list[dict],
            tools: list[dict] | None = None,
            on_token = None,
            on_reasoning = None,
    ) -> LLMResponse:
        params: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            **self.extra,
        }
        if tools:
            params["tools"] = tools
        params["stream_options"] = {"include_usage": True}
        last_err = None
        for attempt in range(3):
            while True:
                try:
                    stream = self._call_with_retry(params)
                    break
                except BadRequestError as e:
                    if _adapt_rejected_param(params, e):
                        continue
                    if "stream_options" not in params:
                        raise
                    params.pop("stream_options")
            try:
                return self._drain(stream, on_token, on_reasoning)
            except (RateLimitError, APITimeoutError, APIConnectionError) as e:
                last_err = e
            except APIError as e:
                status_code = getattr(e, "http_status", None)
                if not status_code or status_code < 500:
                    raise
                last_err = e
            if attempt < 2:
                time.sleep(2 ** attempt)
        raise last_err

    def _drain(self, stream, on_token, on_reasoning) -> LLMResponse:
        content_parts: list[str] = []
        tc_map: dict[int, dict] = {}
        prompt_tok = 0
        completion_tok = 0

        for chunk in stream:
            usage = getattr(chunk, "usage", None)
            if usage:
                prompt_tok = getattr(usage, "prompt_tokens", 0) or 0
                completion_tok = getattr(usage, "completion_tokens", 0) or 0

            if not getattr(chunk, "choices", None):
                continue
            delta = chunk.choices[0].delta

            reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
            if reasoning and on_reasoning:
                on_reasoning(reasoning)

            if getattr(delta, "content", None):
                content_parts.append(delta.content)
                if on_token:
                    on_token(delta.content)

            if getattr(delta, "tool_calls", None):
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tc_map:
                        tc_map[idx] = {"id": "", "name": "", "args": ""}
                    if tc_delta.id:
                        tc_map[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tc_map[idx]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tc_map[idx]["args"] += tc_delta.function.arguments
        parsed: list[ToolCall] = []
        for idx in sorted(tc_map):
            raw = tc_map[idx]
            try:
                args = json.loads(raw["args"])
            except (json.JSONDecodeError, KeyError):
                args = {}
            parsed.append(ToolCall(id=raw["id"], name=raw["name"], arguments=args))
        self.total_prompt_tokens += prompt_tok
        self.total_completion_tokens += completion_tok

        return LLMResponse(
            content="".join(content_parts),
            tool_calls=parsed,
            prompt_tokens=prompt_tok,
            completion_tokens=completion_tok,
        )

    def _call_with_retry(self, params: dict, max_retries: int = 3):
        for attempt in range(max_retries):
            try:
                return self.client.chat.completions.create(**params)
            except (RateLimitError, APITimeoutError, APIConnectionError):
                if attempt == max_retries - 1:
                    raise
                wait = 2 ** attempt
                time.sleep(wait)
            except APIError as e:
                status_code = getattr(e, "http_status", None)
                if status_code and status_code >= 500 and attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise

class LiteLLM(LLM):
    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        **kwargs,
    ):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.extra = kwargs
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    def _call_with_retry(self, params: dict, max_retries: int = 3):
        
        import litellm

        params["drop_params"] = True
        if self.api_key:
            params["api_key"] = self.api_key
        if self.base_url:
            params["api_base"] = self.base_url

        for attempt in range(max_retries):
            try:
                return litellm.completion(**params)
            except Exception as e:
                err = str(e).lower()
                is_transient = any(
                    kw in err
                    for kw in ["rate_limit", "timeout", "connection", "502", "503", "529"]
                )
                is_server = any(kw in err for kw in ["500", "502", "503", "504"])
                if (is_transient or is_server) and attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise