# Re-exported so existing imports (from connectors.gmail import get_gmail_service,
# GMAIL_READONLY, ...) keep working; auth itself lives in connectors.google_auth.
from connectors.google_auth import (  # noqa: F401
    CALENDAR_EVENTS,
    GMAIL_READONLY,
    get_gmail_service,
    get_google_service,
)


def _walk_parts(payload: dict):
    yield payload
    for part in payload.get("parts", []):
        yield from _walk_parts(part)


def _pdf_to_text(data: bytes) -> str:
    import io

    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        return ""


def _extract_text(payload: dict) -> str:
    """Recursively pull the text from a Gmail message payload, preferring
    text/plain over text/html."""
    import base64

    def decode(part):
        data = part.get("body", {}).get("data")
        return base64.urlsafe_b64decode(data).decode("utf-8", "replace") if data else ""

    if payload.get("mimeType") == "text/plain":
        return decode(payload)
    for part in payload.get("parts", []):
        text = _extract_text(part)
        if text.strip():
            return text
    return decode(payload)  # html-only or single-part fallback


class GmailClient:
    def __init__(self, service):
        self._service = service

    def get_unread(self, max_results: int = 20) -> list[dict]:
        """Return metadata for unread emails, most recent first."""
        return self._list_messages(max_results, label_ids=["UNREAD"])

    def search(self, query: str, max_results: int = 10) -> list[dict]:
        """Return metadata for emails matching a Gmail search query."""
        return self._list_messages(max_results, query=query)

    def get_full_text(self, message_id: str) -> str:
        """Return the plain-text body of a message (falls back to whatever text
        part exists). Needed because event details live in the body, not metadata."""
        msg = (
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        return _extract_text(msg.get("payload", {}))

    def get_pdf_text(self, message_id: str) -> str:
        """Extract text from all PDF attachments on a message (empty if none).
        Used as a fallback when payment details aren't in the email body."""
        import base64

        msg = (
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        texts = []
        for part in _walk_parts(msg.get("payload", {})):
            is_pdf = part.get("mimeType") == "application/pdf" or \
                part.get("filename", "").lower().endswith(".pdf")
            if not is_pdf:
                continue
            body = part.get("body", {})
            data = body.get("data")
            if not data and body.get("attachmentId"):
                att = (
                    self._service.users().messages().attachments()
                    .get(userId="me", messageId=message_id, id=body["attachmentId"])
                    .execute()
                )
                data = att.get("data")
            if data:
                texts.append(_pdf_to_text(base64.urlsafe_b64decode(data)))
        return "\n\n".join(t for t in texts if t.strip())

    def _list_messages(self, max_results: int, label_ids: list[str] = None, query: str = None) -> list[dict]:
        kwargs = {"userId": "me", "maxResults": max_results}
        if label_ids:
            kwargs["labelIds"] = label_ids
        if query:
            kwargs["q"] = query

        result = self._service.users().messages().list(**kwargs).execute()
        return [self._get_email(msg["id"]) for msg in result.get("messages", [])]

    def _get_email(self, message_id: str) -> dict:
        detail = (
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format="metadata",
                 metadataHeaders=["Subject", "From", "Date"])
            .execute()
        )
        headers = {h["name"]: h["value"] for h in detail["payload"]["headers"]}
        return {
            "id": message_id,
            "subject": headers.get("Subject", "(no subject)"),
            "from": headers.get("From", ""),
            "date": headers.get("Date", ""),
            "snippet": detail.get("snippet", ""),
        }
