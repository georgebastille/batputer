import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from llm.mlx_client import ChatResult
from persistence.store import ConversationStore
from tasks.invoice_extractor import InvoiceExtractorTask, _parse

FULL = {
    "is_invoice": True, "payee": "Acme Plumbing", "amount": "£120.00",
    "due_date": "2026-07-01", "invoice_number": "INV-42",
    "sort_code": "12-34-56", "account_number": "12345678",
}
NO_BANK = {**FULL, "sort_code": "", "account_number": ""}


def _gmail(body="invoice text", pdf_text=""):
    g = MagicMock()
    g.get_unread.return_value = [{"id": "1", "from": "billing@acme.com", "subject": "Invoice"}]
    g.get_full_text.return_value = body
    g.get_pdf_text.return_value = pdf_text
    return g


def _client(*payloads):
    c = MagicMock()
    c.generate.side_effect = [ChatResult(content=json.dumps(p)) for p in payloads]
    return c


def _connector():
    c = MagicMock()
    c.send_message = AsyncMock()
    return c


def _task(gmail, client, connector, store):
    return InvoiceExtractorTask(accounts=[("primary", gmail)], client=client, model="m",
                                connector=connector, store=store, chat_id=42)


def test_parse_handles_fences_and_garbage():
    assert _parse('```json\n{"is_invoice": false}\n```') == {"is_invoice": False}
    assert _parse("not json") == {}


def test_invoice_with_full_details_announced_without_pdf():
    gmail = _gmail()
    connector = _connector()
    store = ConversationStore(":memory:")
    task = _task(gmail, _client(FULL), connector, store)

    asyncio.run(task.run(None))

    gmail.get_pdf_text.assert_not_called()  # body had everything
    msg = connector.send_message.call_args.args[1]
    assert "Acme Plumbing" in msg and "12-34-56" in msg and "12345678" in msg and "INV-42" in msg
    assert store.filter_unseen(["inv:primary:1"]) == []


def test_pdf_fallback_when_bank_details_missing_from_body():
    gmail = _gmail(pdf_text="Sort code 99-88-77 Account 87654321")
    connector = _connector()
    # first extraction (body) lacks bank details; second (body+pdf) has them
    enriched = {**FULL, "sort_code": "99-88-77", "account_number": "87654321"}
    task = _task(gmail, _client(NO_BANK, enriched), connector, ConversationStore(":memory:"))

    asyncio.run(task.run(None))

    gmail.get_pdf_text.assert_called_once()
    msg = connector.send_message.call_args.args[1]
    assert "99-88-77" in msg and "87654321" in msg


def test_non_invoice_is_ignored():
    gmail = _gmail()
    connector = _connector()
    store = ConversationStore(":memory:")
    task = _task(gmail, _client({"is_invoice": False}), connector, store)

    asyncio.run(task.run(None))

    connector.send_message.assert_not_called()
    gmail.get_pdf_text.assert_not_called()
    assert store.filter_unseen(["inv:primary:1"]) == []  # still marked seen


def test_invoice_missing_bank_details_even_after_pdf_warns():
    gmail = _gmail(pdf_text="")  # no pdf
    connector = _connector()
    task = _task(gmail, _client(NO_BANK), connector, ConversationStore(":memory:"))

    asyncio.run(task.run(None))

    msg = connector.send_message.call_args.args[1]
    assert "Could not find full bank details" in msg
