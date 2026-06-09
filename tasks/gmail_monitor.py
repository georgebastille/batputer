import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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


def get_gmail_service():
    import os
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


class GmailMonitorTask:
    def __init__(
        self,
        gmail_service,
        openai_client,
        model: str,
        connector: "TelegramConnector",
        store: "ConversationStore",
        chat_id: int,
    ):
        self._gmail = gmail_service
        self._client = openai_client
        self._model = model
        self._connector = connector
        self._store = store
        self._chat_id = chat_id

    async def run(self, context) -> None:
        try:
            emails = self._get_unread_emails()
        except Exception:
            logger.exception("Gmail fetch failed")
            return

        if not emails:
            return

        new_ids = self._store.filter_unseen([e["id"] for e in emails])
        if not new_ids:
            return

        new_emails = [e for e in emails if e["id"] in new_ids]
        assessment = self._triage(new_emails)
        self._store.mark_seen(new_ids)

        if assessment.strip().lower() == "no action needed.":
            return

        alert = f"Email alert: {assessment}"
        await self._connector.send_message(self._chat_id, alert)
        self._store.save_message(
            self._chat_id, {"role": "assistant", "content": f"[{alert}]"}
        )

    def _get_unread_emails(self) -> list[dict]:
        result = (
            self._gmail.users()
            .messages()
            .list(userId="me", labelIds=["UNREAD"], maxResults=20)
            .execute()
        )
        messages = result.get("messages", [])
        emails = []
        for msg in messages:
            detail = (
                self._gmail.users()
                .messages()
                .get(userId="me", id=msg["id"], format="metadata",
                     metadataHeaders=["Subject", "From", "Date"])
                .execute()
            )
            headers = {h["name"]: h["value"] for h in detail["payload"]["headers"]}
            emails.append({
                "id": msg["id"],
                "subject": headers.get("Subject", "(no subject)"),
                "from": headers.get("From", ""),
                "date": headers.get("Date", ""),
                "snippet": detail.get("snippet", ""),
            })
        return emails

    def _triage(self, emails: list[dict]) -> str:
        email_list = "\n".join(
            f"- From: {e['from']}\n  Subject: {e['subject']}\n  {e['snippet']}"
            for e in emails
        )
        messages = [
            {"role": "system", "content": _TRIAGE_SYSTEM},
            {"role": "user", "content": f"New emails:\n{email_list}"},
        ]
        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            extra_body={"thinking": {"type": "disabled"}},
        )
        return response.choices[0].message.content
