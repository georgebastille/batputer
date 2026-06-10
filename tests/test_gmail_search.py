import asyncio
from unittest.mock import MagicMock

import tools.gmail_search as gs
from tools.commons import Result, Status


def _mock_openai(text: str):
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=text))]
    )
    return client


async def _collect(agen):
    return [item async for item in agen]


def test_search_emails_not_configured():
    gs.configure(None, None, "test-model")
    items = asyncio.run(_collect(gs.search_emails("test")))
    assert items == [Result("Gmail is not configured.")]


def test_search_emails_returns_summary():
    gmail = MagicMock()
    gmail.search.return_value = [
        {"id": "e1", "from": "alice@example.com", "subject": "Meeting", "date": "Mon", "snippet": "Can we meet?"},
    ]
    client = _mock_openai("Alice asked about meeting up.")
    gs.configure(gmail, client, "test-model")

    items = asyncio.run(_collect(gs.search_emails("from:alice", max_results=5)))

    gmail.search.assert_called_once_with("from:alice", max_results=5)
    statuses = [i.text for i in items if isinstance(i, Status)]
    assert any("Searching Gmail" in s for s in statuses)
    assert any("Found 1 email(s)" in s for s in statuses)
    assert items[-1] == Result("Alice asked about meeting up.")


def test_search_emails_no_results():
    gmail = MagicMock()
    gmail.search.return_value = []
    gs.configure(gmail, MagicMock(), "test-model")

    items = asyncio.run(_collect(gs.search_emails("nothing")))

    assert items[-1] == Result("No matching emails found.")


def test_search_emails_failure():
    gmail = MagicMock()
    gmail.search.side_effect = Exception("api error")
    gs.configure(gmail, MagicMock(), "test-model")

    items = asyncio.run(_collect(gs.search_emails("query")))

    assert len(items) == 2  # Status, then Result
    assert items[-1].text.startswith("Gmail search failed:")
