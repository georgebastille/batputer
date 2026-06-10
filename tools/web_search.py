import json
from typing import AsyncIterator

import trafilatura
from ddgs import DDGS

from tools.commons import Result, Status, tool

_CLIENT = None
_MODEL = None
_MAX_PAGE_CHARS = 4000
_MAX_FETCHED_PAGES = 10  # cap on full page fetches per query


def configure(client, model: str) -> None:
    global _CLIENT, _MODEL
    _CLIENT = client
    _MODEL = model


def fetch_page_text(url: str) -> str:
    try:
        downloaded = trafilatura.fetch_url(url)
        return trafilatura.extract(downloaded) or "Failed to extract"
    except Exception:
        return "Failed to extract"


@tool
async def web_search(query: str, max_results: int = 32):
    """Search the web and return a summarised answer with source links.

    Args:
        query: The question or topic to research.
        max_results: Maximum number of search results to retrieve.
    """
    async for item in WebSearchAgent(_CLIENT, _MODEL).run(query, max_results):
        yield item


class WebSearchAgent:
    _TRIAGE_SYSTEM = (
        "You are a research assistant. "
        "Try to answer the query using the search result snippets provided. "
        "If the snippets contain sufficient information, provide a complete answer with source URLs. "
        "If you need to read specific pages in full to give a good answer, respond with ONLY valid JSON: "
        '{"fetch_urls": ["url1", "url2"]}. '
        "Only request pages when snippets are genuinely insufficient."
    )
    _SUMMARISE_SYSTEM = (
        "You are a research assistant. "
        "Summarise the key findings concisely and include source URLs."
    )

    def __init__(self, client, model: str):
        self._client = client
        self._model = model

    async def run(self, query: str, max_results: int) -> AsyncIterator[Status | Result]:
        yield Status(f"Searching for '{query}'...")
        results = self._search(query, max_results)
        if isinstance(results, str):
            yield Result(results)
            return

        snippets = _format_snippets(results)
        yield Status(f"Found {len(results)} results, reviewing...")
        answer, urls_to_fetch = self._triage(query, snippets)

        if not urls_to_fetch:
            yield Result(answer)
            return

        valid_hrefs = {r["href"] for r in results}
        urls_to_fetch = [u for u in urls_to_fetch if u in valid_hrefs][:_MAX_FETCHED_PAGES]

        page_parts = []
        for i, url in enumerate(urls_to_fetch, 1):
            yield Status(f"Reading page {i} of {len(urls_to_fetch)}: {url}")
            text = fetch_page_text(url)
            page_parts.append(f"URL: {url}\n{text[:_MAX_PAGE_CHARS]}")
        page_content = "\n\n".join(page_parts)

        yield Status("Summarising findings...")
        summary = self._summarise(query, snippets + "\n\n--- Full page content ---\n\n" + page_content)
        yield Result(summary)

    def _search(self, query: str, max_results: int):
        try:
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=max_results))
        except Exception as e:
            return f"Search unavailable: {e}"

    def _triage(self, query: str, snippets: str) -> tuple[str, list]:
        response = self._chat([
            {"role": "system", "content": self._TRIAGE_SYSTEM},
            {"role": "user", "content": f"Query: {query}\n\nSearch results:\n{snippets}"},
        ])
        try:
            stripped = response.strip()
            if stripped.startswith("{"):
                data = json.loads(stripped)
                if "fetch_urls" in data:
                    return "", data["fetch_urls"]
        except (json.JSONDecodeError, KeyError):
            pass
        return response, []

    def _summarise(self, query: str, content: str) -> str:
        return self._chat([
            {"role": "system", "content": self._SUMMARISE_SYSTEM},
            {"role": "user", "content": f"Query: {query}\n\n{content}"},
        ])

    def _chat(self, messages: list) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            extra_body={"thinking": {"type": "disabled"}},
        )
        return response.choices[0].message.content


def _format_snippets(results: list) -> str:
    return "\n\n".join(
        f"[{i}] {r['title']}\nURL: {r['href']}\n{r.get('body', '')}"
        for i, r in enumerate(results, 1)
    )


