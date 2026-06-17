import asyncio
from unittest.mock import MagicMock, patch

from agent import BatPuter, _split_channels
from llm.mlx_client import ChatResult, ToolCall
from persistence.store import ConversationStore
from tools.commons import Result, Status


def _mock_client_sequence(*responses):
    """Build a client mock whose generate() returns ChatResults in sequence."""
    client = MagicMock()
    side_effects = []
    for r in responses:
        if isinstance(r, str):
            side_effects.append(ChatResult(content=r, tool_calls=[], finish_reason="stop"))
        else:
            # r is a list of ToolCall objects
            side_effects.append(ChatResult(content=None, tool_calls=r, finish_reason="tool_calls"))
    client.generate.side_effect = side_effects
    return client


def _make_tool_call(name: str, args: dict, call_id: str = "tc1"):
    return ToolCall(id=call_id, name=name, arguments=args)


class _FakeMemory:
    def __init__(self, profile: str = ""):
        self._profile = profile

    def get_profile(self) -> str:
        return self._profile

    def search(self, query, limit: int = 5):
        return []


async def _collect(agen):
    return [item async for item in agen]


def test_tool_call_loop():
    tool_call = _make_tool_call("web_search", {"query": "test"})
    client = _mock_client_sequence([tool_call], "Final answer based on search")
    store = ConversationStore(":memory:")

    with patch("agent.TOOL_CALLABLES", {"web_search": lambda query: "Mocked result"}):
        agent = BatPuter(client, "test-model", store, _FakeMemory())
        items = asyncio.run(_collect(agent.process_message(1, "search for something")))

    statuses = [i for i in items if isinstance(i, Status)]
    results = [i for i in items if isinstance(i, Result)]
    assert any("web_search" in s.text for s in statuses)
    assert results == [Result("Final answer based on search")]
    assert client.generate.call_count == 2


def test_tool_call_with_status_generator():
    """Async-generator tools forward their Status updates and final Result."""
    tool_call = _make_tool_call("web_search", {"query": "test"})
    client = _mock_client_sequence([tool_call], "Final answer")
    store = ConversationStore(":memory:")

    async def fake_web_search(query):
        yield Status("Searching...")
        yield Result("search summary")

    with patch("agent.TOOL_CALLABLES", {"web_search": fake_web_search}):
        agent = BatPuter(client, "test-model", store, _FakeMemory())
        items = asyncio.run(_collect(agent.process_message(1, "search for something")))

    statuses = [i.text for i in items if isinstance(i, Status)]
    assert "Searching..." in statuses
    results = [i for i in items if isinstance(i, Result)]
    assert results == [Result("Final answer")]


def test_final_iteration_disables_tools_to_force_reply():
    # Model keeps requesting tools; on the last iteration tools are disabled so it
    # must answer instead of dead-ending in "I got stuck in a loop."
    tc = _make_tool_call("web_search", {"query": "x"})
    client = _mock_client_sequence([tc], [tc], [tc], [tc], "Here is your answer.")
    store = ConversationStore(":memory:")
    with patch("agent.TOOL_CALLABLES", {"web_search": lambda query: "result"}):
        agent = BatPuter(client, "test-model", store, _FakeMemory())
        items = asyncio.run(_collect(agent.process_message(1, "do something")))

    results = [i for i in items if isinstance(i, Result)]
    assert results == [Result("Here is your answer.")]
    assert client.generate.call_args_list[-1].kwargs["tools"] is None  # tools off on last try


def test_no_tool_call():
    client = _mock_client_sequence("Just a plain reply")
    store = ConversationStore(":memory:")
    agent = BatPuter(client, "test-model", store, _FakeMemory())
    items = asyncio.run(_collect(agent.process_message(1, "hello")))
    assert items == [Result("Just a plain reply")]
    assert client.generate.call_count == 1


def test_context_persists_across_messages():
    client = _mock_client_sequence("Reply 1", "Reply 2")
    store = ConversationStore(":memory:")
    agent = BatPuter(client, "test-model", store, _FakeMemory())
    asyncio.run(_collect(agent.process_message(1, "first")))
    asyncio.run(_collect(agent.process_message(1, "second")))
    history = store.load(1)
    contents = [m["content"] for m in history if m["role"] != "system" and m["content"]]
    assert "first" in contents
    assert "Reply 1" in contents
    assert "second" in contents


def test_compaction_yields_status():
    client = _mock_client_sequence("Reply", "summary of older messages")
    store = ConversationStore(":memory:")
    agent = BatPuter(client, "test-model", store, _FakeMemory())
    agent.CONTEXT_TOKEN_BUDGET = 0  # force compaction

    store.replace_all(1, [agent._make_system_prompt(1)] + [
        {"role": "user", "content": f"message {i}"} for i in range(8)
    ])

    items = asyncio.run(_collect(agent.process_message(1, "trigger")))

    statuses = [i.text for i in items if isinstance(i, Status)]
    assert "Compacting conversation history..." in statuses
    assert items[-1] == Result("Reply")


def test_thinking_channel_yielded_as_status_and_stripped_from_reply():
    raw = (
        "<|channel>thought\nI should save this to memory."
        "<channel|>I've saved that for you."
    )
    client = _mock_client_sequence(raw)
    store = ConversationStore(":memory:")
    agent = BatPuter(client, "test-model", store, _FakeMemory())
    items = asyncio.run(_collect(agent.process_message(1, "remember this")))

    statuses = [i.text for i in items if isinstance(i, Status)]
    assert "Thinking: I should save this to memory." in statuses
    assert items[-1] == Result("I've saved that for you.")


def test_system_prompt_includes_profile_memories_when_present():
    client = _mock_client_sequence("Reply")
    store = ConversationStore(":memory:")
    memory = _FakeMemory("Daughter's name is Mia, age 8")
    agent = BatPuter(client, "test-model", store, memory)

    prompt = agent._make_system_prompt(1)
    assert "Daughter's name is Mia, age 8" in prompt["content"]
    assert "What you know about the user and their family" in prompt["content"]


def test_image_message_gets_text_only_reply():
    client = _mock_client_sequence("I can't view images right now, but happy to chat!")
    store = ConversationStore(":memory:")
    agent = BatPuter(client, "test-model", store, _FakeMemory())
    items = asyncio.run(_collect(agent.process_message(1, "what is this?", image_data_url="data:image/jpeg;base64,xxx")))

    assert items == [Result("I can't view images right now, but happy to chat!")]
    sent_messages = client.generate.call_args.args[0]
    user_msg = next(m for m in sent_messages if m["role"] == "user")
    assert isinstance(user_msg["content"], str)
    assert "can't view images" in user_msg["content"]


def test_split_channels_parses_gemma_format():
    raw = "<|channel>thought\nWork it out: 3 days.<channel|>You run out in 3 days."
    thinking, final = _split_channels(raw)
    assert thinking == "Work it out: 3 days."
    assert final == "You run out in 3 days."


def test_split_channels_truncated_thought_has_no_final():
    # Generation cut off mid-thought (finish_reason="length"): no close tag.
    thinking, final = _split_channels("<|channel>thought\npartial reasoning")
    assert thinking == "partial reasoning"
    assert final == ""


def test_split_channels_plain_text_passthrough():
    assert _split_channels("Just a reply.") == (None, "Just a reply.")


def test_system_prompt_omits_profile_memories_when_empty():
    client = _mock_client_sequence("Reply")
    store = ConversationStore(":memory:")
    agent = BatPuter(client, "test-model", store, _FakeMemory(""))

    prompt = agent._make_system_prompt(1)
    assert "What you know about the user and their family" not in prompt["content"]


