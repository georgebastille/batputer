import datetime
import json
import logging
import zoneinfo

import openai

from persistence.store import ConversationStore
from tools.commons import TOOL_CALLABLES, TOOLS_REGISTRY

logger = logging.getLogger(__name__)
LONDON_TZ = zoneinfo.ZoneInfo("Europe/London")


class BatPuter:
    CONTEXT_TOKEN_BUDGET = 100_000
    MAX_TOOL_ITERATIONS = 5

    def __init__(self, openai_client, model: str, store: ConversationStore):
        self._client = openai_client
        self._model = model
        self._store = store

    async def process_message(self, chat_id: int, text: str) -> str:
        try:
            return await self._run(chat_id, text)
        except openai.APIConnectionError:
            raise RuntimeError("Cannot reach the local LLM. Is LM Studio running?")
        except openai.APIStatusError as e:
            raise RuntimeError(f"LLM error {e.status_code}")

    async def _run(self, chat_id: int, text: str) -> str:
        messages = self._load_context(chat_id)
        messages.append({"role": "user", "content": text})
        self._store.save_message(chat_id, messages[-1])

        reply = None
        for _ in range(self.MAX_TOOL_ITERATIONS):
            text_reply, tool_calls = self._chat_with_tools(messages)
            if tool_calls:
                tool_calls_data = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in tool_calls
                ]
                assistant_msg = {"role": "assistant", "content": None, "tool_calls": tool_calls_data}
                messages.append(assistant_msg)
                self._store.save_message(chat_id, assistant_msg)
                for tc in tool_calls:
                    result = self._dispatch_tool(tc)
                    tool_msg = {
                        "role": "tool",
                        "content": result,
                        "tool_call_id": tc.id,
                    }
                    messages.append(tool_msg)
                    self._store.save_message(chat_id, tool_msg)
            elif text_reply:
                reply = text_reply
                messages.append({"role": "assistant", "content": reply})
                self._store.save_message(chat_id, messages[-1])
                break

        if reply is None:
            return "I got stuck in a loop. Please try again."

        self._maybe_compress(chat_id, messages)
        return reply

    def _load_context(self, chat_id: int) -> list[dict]:
        messages = self._store.load(chat_id)
        if not messages or messages[0]["role"] != "system":
            messages.insert(0, self._make_system_prompt())
        return messages

    def _make_system_prompt(self) -> dict:
        now = datetime.datetime.now(LONDON_TZ)
        return {
            "role": "system",
            "content": (
                "You are a helpful personal assistant named BatPuter. "
                "You have access to tools including web search. "
                "Always respond in plain text with no markdown formatting — no **, __, ||, #, or backticks. "
                f"The current date and time is {now.strftime('%A, %-d %B %Y at %H:%M')} (London, UK)."
            ),
        }

    def _chat_with_tools(self, messages: list) -> tuple:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            tools=TOOLS_REGISTRY,
            tool_choice="auto",
            extra_body={"thinking": {"type": "disabled"}},
        )
        choice = response.choices[0]
        if choice.finish_reason == "tool_calls":
            return None, choice.message.tool_calls
        return choice.message.content, None

    def _dispatch_tool(self, tool_call) -> str:
        name = tool_call.function.name
        fn = TOOL_CALLABLES.get(name)
        if fn is None:
            return f"Unknown tool: {name}"
        try:
            args = json.loads(tool_call.function.arguments)
            return str(fn(**args))
        except Exception as e:
            return f"Tool {name} failed: {e}"

    def _estimate_tokens(self, messages: list) -> int:
        return sum(len(str(m.get("content") or "")) // 3 + 10 for m in messages)

    def _maybe_compress(self, chat_id: int, messages: list) -> None:
        if self._estimate_tokens(messages) <= self.CONTEXT_TOKEN_BUDGET:
            return
        if len(messages) <= 8:
            return
        middle = messages[1:-6]
        summary = self._raw_chat([
            {
                "role": "system",
                "content": (
                    "Summarise this conversation history concisely. "
                    "Preserve key facts, decisions, and anything the user shared about themselves."
                ),
            },
            {
                "role": "user",
                "content": json.dumps([{"role": m["role"], "content": m.get("content")} for m in middle]),
            },
        ])
        summary_msg = {"role": "system", "content": f"[Prior context]: {summary}"}
        compressed = [messages[0], summary_msg] + messages[-6:]
        messages[:] = compressed
        self._store.replace_all(chat_id, compressed)

    def _raw_chat(self, messages: list) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            extra_body={"thinking": {"type": "disabled"}},
        )
        return response.choices[0].message.content
