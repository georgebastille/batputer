import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

DEFAULT_MAX_TOKENS = 1024


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ChatResult:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"


def _build_result(full_text: str, finish_reason: str, tokenizer, tools=None) -> ChatResult:
    """Build a ChatResult from raw generated text.

    Splits out any `<|tool_call>...<tool_call|>` spans (Gemma 4's native tool-call
    syntax) and parses them via the tokenizer's configured tool parser.
    """
    tool_calls = []
    text_parts = []

    if tokenizer.has_tool_calling and tokenizer.tool_call_start in full_text:
        start, end = tokenizer.tool_call_start, tokenizer.tool_call_end
        remaining = full_text
        i = 0
        while start in remaining:
            before, _, rest = remaining.partition(start)
            text_parts.append(before)
            tool_text, _, remaining = rest.partition(end)
            try:
                parsed = tokenizer.tool_parser(tool_text, tools)
            except (ValueError, json.JSONDecodeError):
                continue
            for call in parsed if isinstance(parsed, list) else [parsed]:
                tool_calls.append(
                    ToolCall(id=f"call_{i}", name=call["name"], arguments=call["arguments"])
                )
                i += 1
        text_parts.append(remaining)
    else:
        text_parts.append(full_text)

    content = "".join(text_parts).strip() or None
    if tool_calls:
        finish_reason = "tool_calls"
    return ChatResult(content=content, tool_calls=tool_calls, finish_reason=finish_reason)


class MLXChatClient:
    """In-process Gemma 4 client backed by MLX.

    All MLX work happens on a single dedicated background thread. MLX's
    generation code keeps a thread-local GPU stream that is bound to the
    thread which first imports `mlx_lm.generate`, so both model loading and
    every `stream_generate` call must run on that same thread.
    """

    def __init__(self, model_path: str):
        self._executor = ThreadPoolExecutor(max_workers=1)
        self.model, self.tokenizer, self.config = self._executor.submit(
            self._load, model_path
        ).result()

    @staticmethod
    def _load(model_path: str):
        from mlx_lm import load

        return load(model_path, return_config=True)

    @property
    def context_length(self) -> int | None:
        return self.config.get("max_position_embeddings") or self.config.get(
            "text_config", {}
        ).get("max_position_embeddings")

    def generate(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        thinking: bool = False,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> ChatResult:
        return self._executor.submit(
            self._generate_impl, messages, tools, thinking, max_tokens
        ).result()

    def _generate_impl(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        thinking: bool,
        max_tokens: int,
    ) -> ChatResult:
        from mlx_lm import stream_generate

        messages = [
            {**m, "content": m.get("content") or ""} if m.get("content") is None else m
            for m in messages
        ]
        prompt = self.tokenizer.apply_chat_template(
            messages,
            tools=tools,
            add_generation_prompt=True,
            enable_thinking=thinking,
            tokenize=True,
        )
        full_text = ""
        finish_reason = "stop"
        for response in stream_generate(self.model, self.tokenizer, prompt, max_tokens=max_tokens):
            full_text += response.text
            if response.finish_reason is not None:
                finish_reason = response.finish_reason

        return _build_result(full_text, finish_reason, self.tokenizer, tools)
