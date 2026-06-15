import json

from mlx_lm.tool_parsers import gemma4

from llm.mlx_client import _build_response, _normalize_messages


class _FakeTokenizer:
    has_tool_calling = True
    tool_call_start = gemma4.tool_call_start
    tool_call_end = gemma4.tool_call_end
    tool_parser = staticmethod(gemma4.parse_tool_call)


def _wrap(text: str) -> str:
    return f"{gemma4.tool_call_start}{text}{gemma4.tool_call_end}"


def test_plain_text_reply():
    response = _build_response("Hello there!", "stop", _FakeTokenizer())
    choice = response.choices[0]
    assert choice.message.content == "Hello there!"
    assert choice.message.tool_calls is None
    assert choice.finish_reason == "stop"


def test_single_tool_call():
    text = "Sure, let me check." + _wrap('call:web_search{query:<|"|>weather<|"|>}')
    response = _build_response(text, "stop", _FakeTokenizer())
    choice = response.choices[0]
    assert choice.finish_reason == "tool_calls"
    assert choice.message.content == "Sure, let me check."
    assert len(choice.message.tool_calls) == 1
    tc = choice.message.tool_calls[0]
    assert tc.function.name == "web_search"
    assert json.loads(tc.function.arguments) == {"query": "weather"}


def test_multiple_tool_calls():
    text = _wrap(
        'call:web_search{query:<|"|>weather<|"|>}'
        'call:remember{fact:<|"|>likes tea<|"|>}'
    )
    response = _build_response(text, "stop", _FakeTokenizer())
    choice = response.choices[0]
    assert choice.finish_reason == "tool_calls"
    names = [tc.function.name for tc in choice.message.tool_calls]
    assert names == ["web_search", "remember"]


def test_thinking_channel_with_tool_call_preserved_in_content():
    text = (
        "<|channel|>analysis<|message|>I should search for this.<|end|>"
        + _wrap('call:web_search{query:<|"|>weather<|"|>}')
    )
    response = _build_response(text, "stop", _FakeTokenizer())
    choice = response.choices[0]
    assert choice.finish_reason == "tool_calls"
    assert "I should search for this." in choice.message.content
    assert len(choice.message.tool_calls) == 1


def test_normalize_messages_handles_content_list_and_none():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "hello"}, {"type": "image_url", "image_url": {}}]},
        {"role": "assistant", "content": None, "tool_calls": [
            {"function": {"name": "web_search", "arguments": '{"query": "x"}'}}
        ]},
    ]
    normalized = _normalize_messages(messages)
    assert normalized[0]["content"] == "hello"
    assert normalized[1]["content"] == ""
    assert normalized[1]["tool_calls"][0]["function"]["arguments"] == {"query": "x"}
