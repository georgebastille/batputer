import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from connectors.gmail import GmailClient
    from connectors.telegram import TelegramConnector
    from persistence.store import ConversationStore

logger = logging.getLogger(__name__)

_TRIAGE_SYSTEM = (
    "You are an email triage assistant. "
    "Identify which emails genuinely require a response or action from the user. "
    "Ignore newsletters, automated notifications, and receipts. "
    "Reply with a brief one or two sentence assessment. "
    'If no action is needed, reply exactly: "No action needed."'
)


class GmailMonitorTask:
    def __init__(
        self,
        gmail_client: "GmailClient",
        client,
        model: str,
        connector: "TelegramConnector",
        store: "ConversationStore",
        chat_id: int,
    ):
        self._gmail = gmail_client
        self._client = client
        self._model = model
        self._connector = connector
        self._store = store
        self._chat_id = chat_id

    async def run(self, context) -> None:
        try:
            emails = self._gmail.get_unread()
        except Exception:
            logger.exception("Gmail fetch failed")
            return

        if not emails:
            return

        new_ids = self._store.filter_unseen([e["id"] for e in emails])
        if not new_ids:
            return

        new_emails = [e for e in emails if e["id"] in new_ids]
        assessment = await self._triage(new_emails)
        self._store.mark_seen(new_ids)

        if assessment.strip().lower() == "no action needed.":
            return

        alert = f"Email alert: {assessment}"
        await self._connector.send_message(self._chat_id, alert)
        self._store.save_message(
            self._chat_id, {"role": "assistant", "content": f"[{alert}]"}
        )

    async def _triage(self, emails: list[dict]) -> str:
        email_list = "\n".join(
            f"- From: {e['from']}\n  Subject: {e['subject']}\n  {e['snippet']}"
            for e in emails
        )
        messages = [
            {"role": "system", "content": _TRIAGE_SYSTEM},
            {"role": "user", "content": f"New emails:\n{email_list}"},
        ]
        result = await asyncio.to_thread(
            self._client.generate,
            messages,
            thinking=False,
        )
        return result.content
