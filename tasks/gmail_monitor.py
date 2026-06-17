import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from connectors.gmail import GmailClient
    from connectors.telegram import TelegramConnector
    from persistence.store import ConversationStore

logger = logging.getLogger(__name__)

_TRIAGE_SYSTEM = (
    "You are an email triage assistant. Notify the user ONLY about emails that "
    "genuinely require an action or a reply from them. "
    "Needs action: a direct message asking a question, an invoice or bill to pay, "
    "a request awaiting their response, a deadline or task they must act on. "
    "Does NOT need action (ignore these): calendar invites, newsletters, marketing, "
    "automated notifications, receipts, confirmations, and anything purely informational. "
    "When in doubt, treat it as no action. "
    "If at least one email needs action, reply with a brief one or two sentence "
    "summary covering only those emails. "
    'If none need action, reply exactly: "No action needed."'
)


class GmailMonitorTask:
    def __init__(
        self,
        accounts: "list[tuple[str, GmailClient]]",
        client,
        model: str,
        connector: "TelegramConnector",
        store: "ConversationStore",
        chat_id: int,
    ):
        self._accounts = accounts
        self._client = client
        self._model = model
        self._connector = connector
        self._store = store
        self._chat_id = chat_id

    async def run(self, context) -> None:
        for label, gmail in self._accounts:
            try:
                await self._check_account(label, gmail)
            except Exception:
                logger.exception("Gmail check failed for account %s", label)

    async def _check_account(self, label: str, gmail: "GmailClient") -> None:
        emails = gmail.get_unread()
        if not emails:
            return

        # Namespace seen-ids by account so message ids can't collide across accounts.
        by_key = {f"{label}:{e['id']}": e for e in emails}
        new_keys = self._store.filter_unseen(list(by_key))
        if not new_keys:
            return

        new_emails = [by_key[k] for k in new_keys]
        assessment = await self._triage(new_emails)
        self._store.mark_seen(new_keys)

        if assessment.strip().lower() == "no action needed.":
            return

        alert = f"Email alert ({label}): {assessment}"
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
