from typing import AsyncIterator

from tools.commons import Result, Status, SubAgent, tool

_GMAIL = None
_CLIENT = None
_MODEL = None


def configure(gmail_client, client, model: str) -> None:
    global _GMAIL, _CLIENT, _MODEL
    _GMAIL = gmail_client
    _CLIENT = client
    _MODEL = model


@tool
async def search_emails(query: str, max_results: int = 10):
    """Search the user's Gmail and return a summary of matching emails.

    Args:
        query: Gmail search query, supporting Gmail search operators such as
            from:, subject:, after:, and is:unread.
        max_results: Maximum number of emails to consider.
    """
    async for item in GmailSearchAgent(_GMAIL, _CLIENT, _MODEL).run(query, max_results):
        yield item


class GmailSearchAgent(SubAgent):
    _SUMMARISE_SYSTEM = (
        "You are an email assistant. "
        "Summarise the following emails in relation to the user's query, "
        "highlighting senders, subjects, and any action needed."
    )

    def __init__(self, gmail_client, client, model: str):
        super().__init__(client, model)
        self._gmail = gmail_client

    async def run(self, query: str, max_results: int) -> AsyncIterator[Status | Result]:
        if self._gmail is None:
            yield Result("Gmail is not configured.")
            return

        yield Status(f"Searching Gmail for '{query}'...")
        try:
            emails = self._gmail.search(query, max_results=max_results)
        except Exception as e:
            yield Result(f"Gmail search failed: {e}")
            return

        if not emails:
            yield Result("No matching emails found.")
            return

        yield Status(f"Found {len(emails)} email(s), summarising...")
        summary = await self._summarise(query, _format_emails(emails))
        yield Result(summary)

    async def _summarise(self, query: str, formatted: str) -> str:
        return await self._reply(self._SUMMARISE_SYSTEM, f"Query: {query}\n\n{formatted}")


def _format_emails(emails: list) -> str:
    return "\n\n".join(
        f"From: {e['from']}\nSubject: {e['subject']}\nDate: {e['date']}\n{e['snippet']}"
        for e in emails
    )
