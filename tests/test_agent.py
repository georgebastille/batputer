import asyncio
from unittest.mock import MagicMock, patch

from agent import BatPuter
from persistence.store import ConversationStore


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


def test_tool_call_loop():
    tool_call = _make_tool_call("web_search", '{"query": "test"}')
    client = _mock_client_sequence([tool_call], "Final answer based on search")
    store = ConversationStore(":memory:")

    with patch("tools.commons.TOOL_CALLABLES", {"web_search": lambda query: "Mocked result"}):
        agent = BatPuter(client, "test-model", store)
        reply = asyncio.run(agent.process_message(1, "search for something"))

    assert reply == "Final answer based on search"
    assert client.chat.completions.create.call_count == 2


def test_no_tool_call():
    client = _mock_client_sequence("Just a plain reply")
    store = ConversationStore(":memory:")
    agent = BatPuter(client, "test-model", store)
    reply = asyncio.run(agent.process_message(1, "hello"))
    assert reply == "Just a plain reply"
    assert client.chat.completions.create.call_count == 1


def test_context_persists_across_messages():
    client = _mock_client_sequence("Reply 1", "Reply 2")
    store = ConversationStore(":memory:")
    agent = BatPuter(client, "test-model", store)
    asyncio.run(agent.process_message(1, "first"))
    asyncio.run(agent.process_message(1, "second"))
    history = store.load(1)
    contents = [m["content"] for m in history if m["role"] != "system" and m["content"]]
    assert "first" in contents
    assert "Reply 1" in contents
    assert "second" in contents
