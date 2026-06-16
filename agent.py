import asyncio
import datetime
import inspect
import json
import logging
import re
import zoneinfo
from typing import AsyncIterator

from persistence.store import ConversationStore
from tools.commons import TOOL_CALLABLES, TOOLS_REGISTRY, Result, Status

logger = logging.getLogger(__name__)
LONDON_TZ = zoneinfo.ZoneInfo("Europe/London")

_CHANNEL_OPEN = "<|channel>"
_CHANNEL_CLOSE = "<channel|>"
_THINKING_CHANNELS = {"analysis", "thought", "thinking"}
_STRAY_CHANNEL_RE = re.compile(r"<\|?channel\|?>")


def _split_channels(content: str | None) -> tuple[str | None, str]:
    """Split Gemma channel markup into (thinking, final) text.

    Gemma 4 emits reasoning and the final reply as separate "channels" in one
    content string: an open token ``<|channel>`` is followed by the channel
    name (e.g. ``thought``) and a newline, the channel body, then a close token
    ``<channel|>``; text after the close is the next channel (typically the
    final reply, which carries no markup). Returns the reasoning text (or None
    if absent) and the cleaned final text.
    """
    if not content:
        return None, content or ""
    if _CHANNEL_OPEN not in content:
        return None, content

    thinking_parts: list[str] = []
    final_parts: list[str] = []
    remaining = content
    while _CHANNEL_OPEN in remaining:
        before, _, rest = remaining.partition(_CHANNEL_OPEN)
        final_parts.append(before)
        block, sep, remaining = rest.partition(_CHANNEL_CLOSE)
        if not sep:
            # Unterminated channel (e.g. generation truncated mid-thought).
            remaining = ""
        name, _, body = block.partition("\n")
        bucket = thinking_parts if name.strip() in _THINKING_CHANNELS else final_parts
        bucket.append(body.strip())
    final_parts.append(remaining)

    thinking = "\n".join(p for p in thinking_parts if p) or None
    final = _STRAY_CHANNEL_RE.sub("", "".join(final_parts)).strip()
    return thinking, final


class BatPuter:
    CONTEXT_TOKEN_BUDGET = 100_000
    MAX_TOOL_ITERATIONS = 5
    TOOL_CALL_REASONING_BUDGET = None

    def __init__(self, client, model: str, store: ConversationStore, memory):
        self._client = client
        self._model = model
        self._store = store
        self._memory = memory

    async def process_message(
        self, chat_id: int, text: str, image_data_url: str | None = None
    ) -> AsyncIterator[Status | Result]:
        try:
            async for item in self._run(chat_id, text, image_data_url):
                yield item
        except Exception as e:
            raise RuntimeError(f"Local model error: {e}")

    async def _run(
        self, chat_id: int, text: str, image_data_url: str | None = None
    ) -> AsyncIterator[Status | Result]:
        if image_data_url is not None:
            text = f"{text}\n(Note: I can't view images right now.)" if text else (
                "(sent a photo, no caption) (Note: I can't view images right now.)"
            )
        messages = self._load_context(chat_id)
        messages.append({"role": "user", "content": text})
        self._store.save_message(chat_id, messages[-1])

        for i in range(self.MAX_TOOL_ITERATIONS):
            text_reply, tool_calls, thinking = await self._chat_with_tools(
                messages, reasoning=self.TOOL_CALL_REASONING_BUDGET
            )
            if thinking:
                yield Status(f"Thinking: {thinking}")
            if tool_calls:
                tool_calls_data = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": tc.arguments},
                    }
                    for tc in tool_calls
                ]
                assistant_msg = {"role": "assistant", "content": None, "tool_calls": tool_calls_data}
                messages.append(assistant_msg)
                self._store.save_message(chat_id, assistant_msg)
                for tc in tool_calls:
                    yield Status(f"Using {tc.name}...")
                    tool_result = ""
                    async for item in self._dispatch_tool(tc):
                        if isinstance(item, Result):
                            tool_result = item.text
                        else:
                            yield item
                    tool_msg = {
                        "role": "tool",
                        "content": tool_result,
                        "tool_call_id": tc.id,
                    }
                    messages.append(tool_msg)
                    self._store.save_message(chat_id, tool_msg)
            elif text_reply:
                messages.append({"role": "assistant", "content": text_reply})
                self._store.save_message(chat_id, messages[-1])
                async for item in self._maybe_compress(chat_id, messages):
                    yield item
                yield Result(text_reply)
                return

        yield Result("I got stuck in a loop. Please try again.")

    def _load_context(self, chat_id: int) -> list[dict]:
        messages = self._store.load(chat_id)
        if not messages or messages[0]["role"] != "system":
            messages.insert(0, self._make_system_prompt(chat_id))
        return messages

    def _make_system_prompt(self, chat_id: int) -> dict:
        now = datetime.datetime.now(LONDON_TZ)
        content = (
            "You are a helpful personal assistant named BatPuter. "
            "You have access to tools including web search. "
            "You cannot view images right now — if the user sends a photo, let them know. "
            "Always respond in plain text with no markdown formatting — no **, __, ||, #, or backticks. "
            f"The current date and time is {now.strftime('%A, %-d %B %Y at %H:%M')} (London, UK). "
            "Use remember to save important new facts about the user or their family — "
            "set profile=True for stable core facts (names, relationships, long-term preferences), "
            "profile=False for situational notes (e.g. plans, things tried, feedback on suggestions). "
            "Use recall_memory to look up relevant past notes when it would help answer the "
            "current question or make a better suggestion."
        )
        profile = self._memory.get_profile()
        if profile:
            content += "\n\nWhat you know about the user and their family:\n" + profile
        return {"role": "system", "content": content}

    async def _chat_with_tools(self, messages: list, reasoning: int | bool | None = None) -> tuple:
        result = await asyncio.to_thread(
            self._client.generate,
            messages,
            tools=TOOLS_REGISTRY,
            thinking=bool(reasoning),
        )
        if result.finish_reason == "tool_calls":
            thinking, _ = _split_channels(result.content)
            return None, result.tool_calls, thinking
        thinking, final = _split_channels(result.content)
        return final, None, thinking

    async def _dispatch_tool(self, tool_call) -> AsyncIterator[Status | Result]:
        name = tool_call.name
        fn = TOOL_CALLABLES.get(name)
        if fn is None:
            yield Result(f"Unknown tool: {name}")
            return
        try:
            args = tool_call.arguments
            if inspect.isasyncgenfunction(fn):
                async for item in fn(**args):
                    yield item
            else:
                yield Result(str(fn(**args)))
        except Exception as e:
            yield Result(f"Tool {name} failed: {e}")

    def _estimate_tokens(self, messages: list) -> int:
        return sum(len(str(m.get("content") or "")) // 3 + 10 for m in messages)

    async def _maybe_compress(self, chat_id: int, messages: list) -> AsyncIterator[Status]:
        if self._estimate_tokens(messages) <= self.CONTEXT_TOKEN_BUDGET:
            return
        if len(messages) <= 8:
            return
        yield Status("Compacting conversation history...")
        middle = messages[1:-6]
        summary = await self._raw_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Summarise this conversation history concisely. "
                        "Preserve key facts, decisions, and anything the user shared about themselves. "
                        "Preserve any recipes or dishes that were suggested and what they were suggested in response to."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps([{"role": m["role"], "content": m.get("content")} for m in middle]),
                },
            ],
            reasoning=True,
        )
        summary_msg = {"role": "system", "content": f"[Prior context]: {summary}"}
        compressed = [messages[0], summary_msg] + messages[-6:]
        messages[:] = compressed
        self._store.replace_all(chat_id, compressed)

    async def _raw_chat(self, messages: list, reasoning: int | bool | None = None) -> str:
        result = await asyncio.to_thread(
            self._client.generate,
            messages,
            thinking=bool(reasoning),
        )
        return _split_channels(result.content)[1]
