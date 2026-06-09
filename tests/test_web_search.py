import json
from unittest.mock import MagicMock, call, patch

import tools.web_search as ws


def _mock_openai_sequence(*texts):
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        MagicMock(choices=[MagicMock(message=MagicMock(content=t))]) for t in texts
    ]
    return client


def test_web_search_answers_from_snippets():
    """LLM answers directly from snippets — no page fetches."""
    fake_results = [
        {"title": "Result 1", "href": "https://example.com/1", "body": "snippet 1"},
        {"title": "Result 2", "href": "https://example.com/2", "body": "snippet 2"},
    ]
    client = _mock_openai_sequence("Summary of findings. Source: https://example.com/1")
    ws.configure(client, "test-model")

    with patch("tools.web_search.DDGS") as mock_ddgs, \
         patch("tools.web_search.trafilatura.fetch_url") as mock_fetch:
        mock_ddgs.return_value.__enter__.return_value.text.return_value = fake_results
        result = ws.web_search("transformers")

    assert "Summary" in result
    mock_fetch.assert_not_called()  # no pages fetched
    assert client.chat.completions.create.call_count == 1


def test_web_search_fetches_relevant_pages():
    """LLM requests specific pages when snippets are insufficient."""
    fake_results = [
        {"title": "Deep dive", "href": "https://example.com/deep", "body": "brief snippet"},
        {"title": "Other", "href": "https://example.com/other", "body": "other snippet"},
    ]
    fetch_request = json.dumps({"fetch_urls": ["https://example.com/deep"]})
    client = _mock_openai_sequence(fetch_request, "Full summary with details.")
    ws.configure(client, "test-model")

    with patch("tools.web_search.DDGS") as mock_ddgs, \
         patch("tools.web_search.trafilatura.fetch_url", return_value="<html>full text</html>"), \
         patch("tools.web_search.trafilatura.extract", return_value="full page content"):
        mock_ddgs.return_value.__enter__.return_value.text.return_value = fake_results
        result = ws.web_search("deep topic")

    assert result == "Full summary with details."
    assert client.chat.completions.create.call_count == 2


def test_web_search_ignores_urls_not_in_results():
    """URLs the LLM requests that weren't in results are silently dropped."""
    fake_results = [
        {"title": "Safe", "href": "https://example.com/safe", "body": "snippet"},
    ]
    fetch_request = json.dumps({"fetch_urls": ["https://attacker.com/evil", "https://example.com/safe"]})
    client = _mock_openai_sequence(fetch_request, "Answer using only safe URL.")
    ws.configure(client, "test-model")

    fetched_urls = []
    def capture_fetch(url):
        fetched_urls.append(url)
        return "<html>ok</html>"

    with patch("tools.web_search.DDGS") as mock_ddgs, \
         patch("tools.web_search.trafilatura.fetch_url", side_effect=capture_fetch), \
         patch("tools.web_search.trafilatura.extract", return_value="content"):
        mock_ddgs.return_value.__enter__.return_value.text.return_value = fake_results
        ws.web_search("something")

    assert "https://attacker.com/evil" not in fetched_urls
    assert "https://example.com/safe" in fetched_urls


def test_web_search_ddgs_failure():
    client = _mock_openai_sequence("irrelevant")
    ws.configure(client, "test-model")

    with patch("tools.web_search.DDGS") as mock_ddgs:
        mock_ddgs.return_value.__enter__.side_effect = Exception("network error")
        result = ws.web_search("anything")

    assert result.startswith("Search unavailable:")


def test_fetch_page_text_fallback():
    with patch("tools.web_search.trafilatura.fetch_url", return_value=None), \
         patch("tools.web_search.trafilatura.extract", return_value=None):
        result = ws.fetch_page_text("https://example.com")
    assert result == "Failed to extract"
