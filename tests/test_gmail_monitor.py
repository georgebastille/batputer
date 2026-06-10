import asyncio
from unittest.mock import AsyncMock, MagicMock

from connectors.gmail import GmailClient
from persistence.store import ConversationStore
from tasks.gmail_monitor import GmailMonitorTask


def _make_task(gmail_client, openai_client, connector, store):
    return GmailMonitorTask(
        gmail_client=gmail_client,
        openai_client=openai_client,
        model="test-model",
        connector=connector,
        store=store,
        chat_id=42,
    )


def _gmail_client_returning(emails: list):
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
                    {"name": "Date", "value": ""},
                ]
            },
            "snippet": e.get("snippet", ""),
        }
        return call

    svc.users().messages().get.side_effect = get_detail
    return GmailClient(svc)


def _gmail_client_failing():
    svc = MagicMock()
    svc.users().messages().list().execute.side_effect = Exception("network error")
    return GmailClient(svc)


def _openai_returning(text: str):
    client = MagicMock()
    client.chat.completions.create.return_value.choices[0].message.content = text
    return client


def test_new_emails_trigger_alert():
    emails = [
        {"id": "e1", "subject": "Meeting", "from": "alice@example.com", "snippet": "Can we meet?"},
        {"id": "e2", "subject": "Invoice", "from": "bob@example.com", "snippet": "Please approve."},
    ]
    store = ConversationStore(":memory:")
    connector = MagicMock()
    connector.send_message = AsyncMock()
    client = _openai_returning("2 emails need attention: reply to Alice, approve invoice.")

    task = _make_task(_gmail_client_returning(emails), client, connector, store)
    asyncio.run(task.run(None))

    connector.send_message.assert_called_once()
    history = store.load(42)
    assert any("Email alert" in (m.get("content") or "") for m in history)
    assert set(store.filter_unseen(["e1", "e2"])) == set()


def test_no_new_emails_no_alert():
    emails = [{"id": "e1", "subject": "Old", "from": "x@x.com", "snippet": "old"}]
    store = ConversationStore(":memory:")
    store.mark_seen(["e1"])
    connector = MagicMock()
    connector.send_message = AsyncMock()
    client = _openai_returning("irrelevant")

    task = _make_task(_gmail_client_returning(emails), client, connector, store)
    asyncio.run(task.run(None))

    connector.send_message.assert_not_called()


def test_gmail_failure_no_crash():
    store = ConversationStore(":memory:")
    connector = MagicMock()
    connector.send_message = AsyncMock()
    client = _openai_returning("irrelevant")

    task = _make_task(_gmail_client_failing(), client, connector, store)
    asyncio.run(task.run(None))

    connector.send_message.assert_not_called()


def test_no_action_needed_no_alert():
    emails = [{"id": "e3", "subject": "Newsletter", "from": "news@x.com", "snippet": "weekly"}]
    store = ConversationStore(":memory:")
    connector = MagicMock()
    connector.send_message = AsyncMock()
    client = _openai_returning("No action needed.")

    task = _make_task(_gmail_client_returning(emails), client, connector, store)
    asyncio.run(task.run(None))

    connector.send_message.assert_not_called()
