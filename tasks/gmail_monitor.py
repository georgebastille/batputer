import asyncio

from tasks.email_task import PerAccountEmailTask

_TRIAGE_SYSTEM = (
    "You are an email triage assistant. Notify the user ONLY about emails that "
    "genuinely require an action or a reply from them. "
    "Needs action: a direct message asking a question, "
    "a request awaiting their response, a deadline or task they must act on. "
    "Does NOT need action (ignore these): invoices and bills to pay (handled "
    "separately), calendar invites, newsletters, marketing, "
    "automated notifications, receipts, confirmations, and anything purely informational. "
    "When in doubt, treat it as no action. "
    "If at least one email needs action, reply with a brief one or two sentence "
    "summary covering only those emails. "
    'If none need action, reply exactly: "No action needed."'
)


class GmailMonitorTask(PerAccountEmailTask):
    seen_prefix = ""  # keys are "<label>:<id>"

    def __init__(self, accounts, client, model: str, connector, store, chat_id: int):
        super().__init__(accounts, store)
        self._client = client
        self._model = model
        self._connector = connector
        self._chat_id = chat_id

    async def check_account(self, label, gmail) -> None:
        new = self.new_unseen(label, gmail)
        if not new:
            return
        assessment = await self._triage([email for _, email in new])
        self.mark_seen(key for key, _ in new)

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
