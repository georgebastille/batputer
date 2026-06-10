from tools.commons import tool

_GMAIL = None


def configure(gmail_client) -> None:
    global _GMAIL
    _GMAIL = gmail_client


@tool
def search_emails(query: str, max_results: int = 10) -> str:
    """Search the user's Gmail and return matching emails.

    Args:
        query: Gmail search query, supporting Gmail search operators such as
            from:, subject:, after:, and is:unread.
        max_results: Maximum number of emails to return.
    """
    if _GMAIL is None:
        return "Gmail is not configured."
    try:
        emails = _GMAIL.search(query, max_results=max_results)
    except Exception as e:
        return f"Gmail search failed: {e}"
    if not emails:
        return "No matching emails found."
    return "\n\n".join(
        f"From: {e['from']}\nSubject: {e['subject']}\nDate: {e['date']}\n{e['snippet']}"
        for e in emails
    )
