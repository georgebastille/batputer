"""Google OAuth: build authorised API clients (Gmail, Calendar).

Kept separate from the Gmail/Calendar clients so authentication is a single
responsibility — each account/scope-set has its own token file and re-consents
automatically when a new scope (e.g. calendar) is requested.
"""

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
