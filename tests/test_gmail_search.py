from unittest.mock import MagicMock

import tools.gmail_search as gmail_search


def test_search_emails_not_configured():
    gmail_search.configure(None)
    assert gmail_search.search_emails("test") == "Gmail is not configured."


def test_search_emails_returns_formatted_results():
    client = MagicMock()
    client.search.return_value = [
        {"id": "e1", "from": "alice@example.com", "subject": "Meeting", "date": "Mon", "snippet": "Can we meet?"},
    ]
    gmail_search.configure(client)

    result = gmail_search.search_emails("from:alice", max_results=5)

    client.search.assert_called_once_with("from:alice", max_results=5)
    assert "alice@example.com" in result
    assert "Meeting" in result
    assert "Can we meet?" in result


def test_search_emails_no_results():
    client = MagicMock()
    client.search.return_value = []
    gmail_search.configure(client)

    assert gmail_search.search_emails("nothing") == "No matching emails found."


def test_search_emails_failure():
    client = MagicMock()
    client.search.side_effect = Exception("api error")
    gmail_search.configure(client)

    result = gmail_search.search_emails("query")

    assert result.startswith("Gmail search failed:")
