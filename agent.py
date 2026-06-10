import datetime
import inspect
import json
import logging
import zoneinfo
from typing import AsyncIterator

import openai

from persistence.store import ConversationStore
from tools.commons import TOOL_CALLABLES, TOOLS_REGISTRY, Result, Status

logger = logging.getLogger(__name__)
LONDON_TZ = zoneinfo.ZoneInfo("Europe/London")


class BatPuter:
    CONTEXT_TOKEN_BUDGET = 100_000
    MAX_TOOL_ITERATIONS = 5

    def __init__(self, openai_client, model: str, store: ConversationStore):
        self._client = openai_client
        self._model = model
        self._store = store

    async def process_message(
        self, chat_id: int, text: str, image_data_url: str | None = None, sender_name: str | None = None
    ) -> AsyncIterator[Status | Result]:
        try:
            async for item in self._run(chat_id, text, image_data_url, sender_name):
                yield item
        except openai.APIConnectionError:
            raise RuntimeError("Cannot reach the local LLM. Is LM Studio running?")
        except openai.APIStatusError as e:
            raise RuntimeError(f"LLM error {e.status_code}")

    async def _run(
        self, chat_id: int, text: str, image_data_url: str | None = None, sender_name: str | None = None
    ) -> AsyncIterator[Status | Result]:
        messages = self._load_context(chat_id)
        if sender_name is not None:
            text = f"[{sender_name}]: {text}"
        messages.append({"role": "user", "content": text})
        self._store.save_message(chat_id, messages[-1])

        if image_data_url is not None:
            yield Status("Looking at the image...")

        for i in range(self.MAX_TOOL_ITERATIONS):
            llm_messages = messages
            if image_data_url is not None and i == 0:
                llm_messages = messages[:-1] + [_user_message_with_image(text, image_data_url)]
            text_reply, tool_calls = self._chat_with_tools(llm_messages)
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
                    yield Status(f"Using {tc.function.name}...")
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
            "The user can send you images; describe and answer questions about them, "
            "but note that images are not retained in conversation history. "
            "Always respond in plain text with no markdown formatting — no **, __, ||, #, or backticks. "
            f"The current date and time is {now.strftime('%A, %-d %B %Y at %H:%M')} (London, UK). "
            "Use remember to save important new facts about the user or their family — "
            "set profile=True for stable core facts (names, relationships, long-term preferences), "
            "profile=False for situational notes (including food/recipe feedback). "
            "Use recall_memory to look up relevant past notes when it would help "
            "(e.g. before suggesting recipes, or answering questions about plans, "
            "family members, or past conversations)."
        )
        notes = self._store.get_profile_memories(chat_id)
        if notes:
            content += "\n\nWhat you know about the user and their family:\n"
            content += "\n".join(f"- {note}" for note in notes)
        return {"role": "system", "content": content}

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

    async def _dispatch_tool(self, tool_call) -> AsyncIterator[Status | Result]:
        name = tool_call.function.name
        fn = TOOL_CALLABLES.get(name)
        if fn is None:
            yield Result(f"Unknown tool: {name}")
            return
        try:
            args = json.loads(tool_call.function.arguments)
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
        summary = self._raw_chat([
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


def _user_message_with_image(text: str, image_data_url: str) -> dict:
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": image_data_url}},
        ],
    }
