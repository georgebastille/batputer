GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
CALENDAR_EVENTS = "https://www.googleapis.com/auth/calendar.events"


def get_google_service(api: str, version: str, token_path: str, scopes: list[str],
                       credentials_path: str = "credentials.json"):
    """Build an authorised Google API client. Each account/scope-set uses its own
    token file; first use opens a browser for consent. Reused for Gmail and Calendar."""
    import json
    import os
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    granted: set[str] = set()
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, scopes)
        # Read the *granted* scopes from the file — creds.scopes reflects the
        # requested scopes (passed above), not what the token was actually issued for.
        try:
            granted = set(json.load(open(token_path)).get("scopes") or [])
        except (OSError, ValueError):
            granted = set()
    # Re-consent when the stored token is missing any newly-requested scope
    # (e.g. when the calendar scope is added to an existing Gmail token).
    has_scopes = granted.issuperset(scopes)
    if not creds or not creds.valid or not has_scopes:
        if creds and creds.expired and creds.refresh_token and has_scopes:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, scopes)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())
    return build(api, version, credentials=creds)


def get_gmail_service(token_path: str = "token.json", scopes: list[str] = (GMAIL_READONLY,)):
    return get_google_service("gmail", "v1", token_path, list(scopes))


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
