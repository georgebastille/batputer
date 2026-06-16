from mlx_lm.tool_parsers import gemma4

from llm.mlx_client import _build_result


class _FakeTokenizer:
    has_tool_calling = True
    tool_call_start = gemma4.tool_call_start
    tool_call_end = gemma4.tool_call_end
    tool_parser = staticmethod(gemma4.parse_tool_call)


def _wrap(text: str) -> str:
    return f"{gemma4.tool_call_start}{text}{gemma4.tool_call_end}"


def test_plain_text_reply():
    result = _build_result("Hello there!", "stop", _FakeTokenizer())
    assert result.content == "Hello there!"
    assert result.tool_calls == []
    assert result.finish_reason == "stop"


def test_single_tool_call():
    text = "Sure, let me check." + _wrap('call:web_search{query:<|"|>weather<|"|>}')
    result = _build_result(text, "stop", _FakeTokenizer())
    assert result.finish_reason == "tool_calls"
    assert result.content == "Sure, let me check."
    assert len(result.tool_calls) == 1
    tc = result.tool_calls[0]
    assert tc.name == "web_search"
    assert tc.arguments == {"query": "weather"}


def test_multiple_tool_calls():
    text = _wrap(
        'call:web_search{query:<|"|>weather<|"|>}'
        'call:remember{fact:<|"|>likes tea<|"|>}'
    )
    result = _build_result(text, "stop", _FakeTokenizer())
    assert result.finish_reason == "tool_calls"
    names = [tc.name for tc in result.tool_calls]
    assert names == ["web_search", "remember"]
    assert [tc.id for tc in result.tool_calls] == ["call_0", "call_1"]


def test_thinking_channel_with_tool_call_preserved_in_content():
    text = (
        "<|channel>thought\nI should search for this.<channel|>"
        + _wrap('call:web_search{query:<|"|>weather<|"|>}')
    )
    result = _build_result(text, "stop", _FakeTokenizer())
    assert result.finish_reason == "tool_calls"
    assert "I should search for this." in result.content
    assert len(result.tool_calls) == 1
