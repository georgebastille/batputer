from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials


def get_recent_emails(service, max_results=10):
    results = (
        service.users()
        .messages()
        .list(userId="me", q="is:unread", maxResults=max_results)
        .execute()
    )

    emails = []
    for item in results.get("messages", []):
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=item["id"], format="full")
            .execute()
        )

        # extract headers
        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}

        emails.append(
            {
                "id": item["id"],
                "subject": headers.get("Subject", ""),
                "from": headers.get("From", ""),
                "date": headers.get("Date", ""),
                "snippet": msg["snippet"],
            }
        )

    return emails


creds = Credentials.from_authorized_user_file("token.json")
service = build("gmail", "v1", credentials=creds)
print(get_recent_emails(service=service))
