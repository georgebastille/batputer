from unittest.mock import MagicMock

from connectors.gmail import GmailClient


def _make_service(emails: list):
    svc = MagicMock()
    msgs = [{"id": e["id"]} for e in emails]
    svc.users().messages().list().execute.return_value = {"messages": msgs}

    def get_detail(userId, id, format, metadataHeaders):
        e = next(x for x in emails if x["id"] == id)
        call = MagicMock()
        call.execute.return_value = {
            "payload": {
                "headers": [
                    {"name": "Subject", "value": e.get("subject", "")},
                    {"name": "From", "value": e.get("from", "")},
                    {"name": "Date", "value": e.get("date", "")},
                ]
            },
            "snippet": e.get("snippet", ""),
        }
        return call

    svc.users().messages().get.side_effect = get_detail
    return svc


def test_get_unread_uses_unread_label():
    emails = [{"id": "e1", "subject": "Hi", "from": "a@b.com", "snippet": "hello"}]
    svc = _make_service(emails)
    client = GmailClient(svc)

    result = client.get_unread(max_results=5)

    assert result == [{"id": "e1", "subject": "Hi", "from": "a@b.com", "date": "", "snippet": "hello"}]
    svc.users().messages().list.assert_called_with(userId="me", maxResults=5, labelIds=["UNREAD"])


def test_search_uses_query():
    emails = [{"id": "e2", "subject": "Invoice", "from": "billing@b.com", "snippet": "due soon"}]
    svc = _make_service(emails)
    client = GmailClient(svc)

    result = client.search("from:billing@b.com", max_results=3)

    assert result == [{"id": "e2", "subject": "Invoice", "from": "billing@b.com", "date": "", "snippet": "due soon"}]
    svc.users().messages().list.assert_called_with(userId="me", maxResults=3, q="from:billing@b.com")


def test_no_results():
    svc = MagicMock()
    svc.users().messages().list().execute.return_value = {}
    client = GmailClient(svc)

    assert client.search("nothing matches") == []
