SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def get_gmail_service():
    import os
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

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


class GmailClient:
    def __init__(self, service):
        self._service = service

    def get_unread(self, max_results: int = 20) -> list[dict]:
        """Return metadata for unread emails, most recent first."""
        return self._list_messages(max_results, label_ids=["UNREAD"])

    def search(self, query: str, max_results: int = 10) -> list[dict]:
        """Return metadata for emails matching a Gmail search query."""
        return self._list_messages(max_results, query=query)

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
