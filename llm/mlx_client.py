import copy
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

DEFAULT_MAX_TOKENS = 1024


def _normalize_messages(messages: list[dict]) -> list[dict]:
    """Normalize OpenAI-style messages for `apply_chat_template`.

    - `content` lists (e.g. text/image parts) become a plain string (text parts only).
    - `content=None` becomes `""`.
    - Assistant `tool_calls[].function.arguments` JSON strings become dicts.
    """
    normalized = copy.deepcopy(messages)
    for message in normalized:
        content = message.get("content")
        if isinstance(content, list):
            message["content"] = "".join(
                part.get("text", "") for part in content if part.get("type") == "text"
            )
        elif content is None:
            message["content"] = ""

        for tool_call in message.get("tool_calls") or []:
            func = tool_call.get("function")
            if func and isinstance(func.get("arguments"), str):
                func["arguments"] = json.loads(func["arguments"])
    return normalized


def _build_response(full_text: str, finish_reason: str, tokenizer, tools=None):
    """Build an OpenAI-shaped chat completion response from raw generated text.

    Splits out any `<|tool_call>...<tool_call|>` spans (Gemma 4's native tool-call
    syntax) and parses them via the tokenizer's configured tool parser.
    """
    tool_calls = []
    text_parts = []

    if tokenizer.has_tool_calling and tokenizer.tool_call_start in full_text:
        start, end = tokenizer.tool_call_start, tokenizer.tool_call_end
        remaining = full_text
        while start in remaining:
            before, _, rest = remaining.partition(start)
            text_parts.append(before)
            tool_text, _, remaining = rest.partition(end)
            try:
                parsed = tokenizer.tool_parser(tool_text, tools)
            except (ValueError, json.JSONDecodeError):
                continue
            for call in parsed if isinstance(parsed, list) else [parsed]:
                tool_calls.append(call)
        text_parts.append(remaining)
    else:
        text_parts.append(full_text)

    content = "".join(text_parts).strip() or None

    if tool_calls:
        message_tool_calls = [
            SimpleNamespace(
                id=f"call_{i}",
                type="function",
                function=SimpleNamespace(
                    name=call["name"], arguments=json.dumps(call["arguments"])
                ),
            )
            for i, call in enumerate(tool_calls)
        ]
        finish_reason = "tool_calls"
    else:
        message_tool_calls = None

    message = SimpleNamespace(content=content, tool_calls=message_tool_calls)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


class MLXChatClient:
    """In-process drop-in for the subset of the `openai` client used by this app.

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
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    @staticmethod
    def _load(model_path: str):
        from mlx_lm import load

        return load(model_path, return_config=True)

    @property
    def context_length(self) -> int | None:
        return self.config.get("max_position_embeddings") or self.config.get(
            "text_config", {}
        ).get("max_position_embeddings")

    def _create(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        extra_body: dict | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ):
        return self._executor.submit(
            self._create_impl, messages, tools, extra_body, max_tokens
        ).result()

    def _create_impl(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        extra_body: dict | None,
        max_tokens: int,
    ):
        from mlx_lm import stream_generate

        messages = _normalize_messages(messages)
        enable_thinking = (extra_body or {}).get("thinking", {}).get("type") == "enabled"

        prompt = self.tokenizer.apply_chat_template(
            messages,
            tools=tools,
            add_generation_prompt=True,
            enable_thinking=enable_thinking,
            tokenize=True,
        )
        full_text = ""
        finish_reason = "stop"
        for response in stream_generate(self.model, self.tokenizer, prompt, max_tokens=max_tokens):
            full_text += response.text
            if response.finish_reason is not None:
                finish_reason = response.finish_reason

        return _build_response(full_text, finish_reason, self.tokenizer, tools)
