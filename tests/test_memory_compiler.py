import asyncio
from unittest.mock import MagicMock

from llm.mlx_client import ChatResult
from persistence.markdown_memory import MarkdownMemory
from tasks.memory_compiler import MemoryCompilerTask

_COMPILER_OUTPUT = """\
<<<PROFILE>>>
User has a daughter Auri (9).
<<<TOPIC: auri>>>
# Auri

9 years old. Starts swimming on Tuesdays. Related: [[profile]].
<<<INDEX>>>
- [[topics/auri]] — Auri, the user's daughter
<<<END>>>
"""


def _task(memory, client):
    return MemoryCompilerTask(memory, client, "test-model", chat_id=42)


def _client_returning(text: str):
    client = MagicMock()
    client.generate.return_value = ChatResult(content=text)
    return client


def test_compile_writes_pages_and_advances_marker(tmp_path):
    memory = MarkdownMemory(str(tmp_path))
    memory.append_raw("Auri starts swimming on Tuesdays")
    memory.append_raw("Auri is 9", profile=True)
    client = _client_returning(_COMPILER_OUTPUT)

    asyncio.run(_task(memory, client).run(None))

    assert "swimming on Tuesdays" in (memory.topics_dir / "auri.md").read_text()
    assert "daughter Auri" in memory.get_profile()
    assert "[[topics/auri]]" in memory.read_index()
    assert memory.uncompiled_entries() == []  # marker advanced


def test_compile_noop_when_nothing_new(tmp_path):
    memory = MarkdownMemory(str(tmp_path))
    client = _client_returning(_COMPILER_OUTPUT)

    asyncio.run(_task(memory, client).run(None))

    client.generate.assert_not_called()


def test_compile_keeps_marker_when_output_unparseable(tmp_path):
    memory = MarkdownMemory(str(tmp_path))
    memory.append_raw("some fact")
    client = _client_returning("I could not produce any pages, sorry.")

    asyncio.run(_task(memory, client).run(None))

    client.generate.assert_called_once()
    # nothing parseable -> retry next run
    assert len(memory.uncompiled_entries()) == 1


def test_compile_skips_echoed_placeholder_blocks(tmp_path):
    memory = MarkdownMemory(str(tmp_path))
    memory.append_raw("Auri starts swimming on Tuesdays")
    # Model parrots the format template's placeholder slug/body alongside a real page.
    output = (
        "<<<TOPIC: the-page-slug>>>\n...full updated body of that topic page...\n"
        "<<<TOPIC: auri>>>\n# Auri\n\nSwims on Tuesdays.\n<<<END>>>\n"
    )
    asyncio.run(_task(memory, _client_returning(output)).run(None))

    assert (memory.topics_dir / "auri.md").exists()
    assert not (memory.topics_dir / "the-page-slug.md").exists()
    assert not (memory.topics_dir / "slug-name.md").exists()


def test_compile_applies_rename_directive(tmp_path, monkeypatch):
    memory = MarkdownMemory(str(tmp_path))
    memory.append_raw("call aurora by her full name now")
    moves = []
    monkeypatch.setattr(memory, "move_topic", lambda o, n: moves.append((o, n)) or True)
    output = (
        "<<<RENAME: auri || aurora>>>\n"
        "<<<TOPIC: aurora>>>\n# Aurora\n\nFull name used now.\n<<<END>>>\n"
    )
    asyncio.run(_task(memory, _client_returning(output)).run(None))

    assert ("auri", "aurora") in moves
    assert (memory.topics_dir / "aurora.md").exists()


def test_compile_feeds_unresolved_links_into_context(tmp_path):
    memory = MarkdownMemory(str(tmp_path))
    memory.write_topic("auri", "Auri does [[Swimming Club]] on Tuesdays.")  # dangling link
    memory.append_raw("a new fact")
    task = _task(memory, _client_returning(_COMPILER_OUTPUT))
    asyncio.run(task.run(None))

    sent = task._client.generate.call_args.args[0][1]["content"]
    assert "UNRESOLVED LINKS" in sent
    assert "[[Swimming Club]]" in sent


def test_compile_survives_model_error(tmp_path):
    memory = MarkdownMemory(str(tmp_path))
    memory.append_raw("some fact")
    client = MagicMock()
    client.generate.side_effect = RuntimeError("boom")

    # run() swallows exceptions (scheduled job must not crash the loop)
    asyncio.run(_task(memory, client).run(None))
    assert len(memory.uncompiled_entries()) == 1
