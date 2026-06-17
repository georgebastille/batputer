from typing import AsyncIterator

from tools.commons import Result, Status, SubAgent, tool

_ACCOUNTS = []
_CLIENT = None
_MODEL = None


def configure(accounts, client, model: str) -> None:
    """accounts: list of (label, GmailClient)."""
    global _ACCOUNTS, _CLIENT, _MODEL
    _ACCOUNTS = accounts
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
    async for item in GmailSearchAgent(_ACCOUNTS, _CLIENT, _MODEL).run(query, max_results):
        yield item


class GmailSearchAgent(SubAgent):
    _SUMMARISE_SYSTEM = (
        "You are an email assistant. "
        "Summarise the following emails in relation to the user's query, "
        "highlighting senders, subjects, and any action needed."
    )

    def __init__(self, accounts, client, model: str):
        super().__init__(client, model)
        self._accounts = accounts

    async def run(self, query: str, max_results: int) -> AsyncIterator[Status | Result]:
        if not self._accounts:
            yield Result("Gmail is not configured.")
            return

        yield Status(f"Searching Gmail for '{query}'...")
        emails = []
        for label, gmail in self._accounts:
            try:
                for e in gmail.search(query, max_results=max_results):
                    emails.append({**e, "account": label})
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
        f"Account: {e['account']}\nFrom: {e['from']}\nSubject: {e['subject']}\n"
        f"Date: {e['date']}\n{e['snippet']}"
        for e in emails
    )
