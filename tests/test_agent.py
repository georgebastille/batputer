import asyncio
from unittest.mock import MagicMock, patch

from agent import BatPuter
from persistence.store import ConversationStore
from tools.commons import Result, Status


def _mock_client_sequence(*responses):
    """Build an openai mock that returns responses in sequence."""
    client = MagicMock()
    side_effects = []
    for r in responses:
        mock_response = MagicMock()
        if isinstance(r, str):
            mock_response.choices[0].finish_reason = "stop"
            mock_response.choices[0].message.content = r
            mock_response.choices[0].message.tool_calls = None
        else:
            # r is a list of tool_call mocks
            mock_response.choices[0].finish_reason = "tool_calls"
            mock_response.choices[0].message.content = None
            mock_response.choices[0].message.tool_calls = r
        side_effects.append(mock_response)
    client.chat.completions.create.side_effect = side_effects
    return client


def _make_tool_call(name: str, args: str, call_id: str = "tc1"):
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = args
    return tc


async def _collect(agen):
    return [item async for item in agen]


def test_tool_call_loop():
    tool_call = _make_tool_call("web_search", '{"query": "test"}')
    client = _mock_client_sequence([tool_call], "Final answer based on search")
    store = ConversationStore(":memory:")

    with patch("agent.TOOL_CALLABLES", {"web_search": lambda query: "Mocked result"}):
        agent = BatPuter(client, "test-model", store)
        items = asyncio.run(_collect(agent.process_message(1, "search for something")))

    statuses = [i for i in items if isinstance(i, Status)]
    results = [i for i in items if isinstance(i, Result)]
    assert any("web_search" in s.text for s in statuses)
    assert results == [Result("Final answer based on search")]
    assert client.chat.completions.create.call_count == 2


def test_tool_call_with_status_generator():
    """Async-generator tools forward their Status updates and final Result."""
    tool_call = _make_tool_call("web_search", '{"query": "test"}')
    client = _mock_client_sequence([tool_call], "Final answer")
    store = ConversationStore(":memory:")

    async def fake_web_search(query):
        yield Status("Searching...")
        yield Result("search summary")

    with patch("agent.TOOL_CALLABLES", {"web_search": fake_web_search}):
        agent = BatPuter(client, "test-model", store)
        items = asyncio.run(_collect(agent.process_message(1, "search for something")))

    statuses = [i.text for i in items if isinstance(i, Status)]
    assert "Searching..." in statuses
    results = [i for i in items if isinstance(i, Result)]
    assert results == [Result("Final answer")]


def test_no_tool_call():
    client = _mock_client_sequence("Just a plain reply")
    store = ConversationStore(":memory:")
    agent = BatPuter(client, "test-model", store)
    items = asyncio.run(_collect(agent.process_message(1, "hello")))
    assert items == [Result("Just a plain reply")]
    assert client.chat.completions.create.call_count == 1


def test_context_persists_across_messages():
    client = _mock_client_sequence("Reply 1", "Reply 2")
    store = ConversationStore(":memory:")
    agent = BatPuter(client, "test-model", store)
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
    agent = BatPuter(client, "test-model", store)
    agent.CONTEXT_TOKEN_BUDGET = 0  # force compaction

    store.replace_all(1, [agent._make_system_prompt(1)] + [
        {"role": "user", "content": f"message {i}"} for i in range(8)
    ])

    items = asyncio.run(_collect(agent.process_message(1, "trigger")))

    statuses = [i.text for i in items if isinstance(i, Status)]
    assert "Compacting conversation history..." in statuses
    assert items[-1] == Result("Reply")


def test_system_prompt_includes_profile_memories_when_present():
    client = _mock_client_sequence("Reply")
    store = ConversationStore(":memory:")
    store.add_memory(1, "Daughter's name is Mia, age 8", category="profile")
    agent = BatPuter(client, "test-model", store)

    prompt = agent._make_system_prompt(1)
    assert "Daughter's name is Mia, age 8" in prompt["content"]
    assert "What you know about the user and their family" in prompt["content"]


def test_system_prompt_omits_profile_memories_when_empty():
    client = _mock_client_sequence("Reply")
    store = ConversationStore(":memory:")
    agent = BatPuter(client, "test-model", store)

    prompt = agent._make_system_prompt(1)
    assert "What you know about the user and their family" not in prompt["content"]


