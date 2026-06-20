"""Surface invoices/bills with the details needed to pay them.

Per scheduled run, for each Gmail account: for new unread mail, ask the model
whether it's a payable invoice and extract the payee, amount, due date, invoice
number, and bank details (sort code + account number). If the bank details aren't
in the email body, fall back to extracting text from any PDF attachment and try
again. Matching invoices are announced on Telegram with everything needed to pay.
"""
import json
import logging
import re

from tools.commons import SubAgent

logger = logging.getLogger(__name__)

_INVOICE_SYSTEM = (
    "You extract payment details from emails. If the email (and any attached "
    "document text) is an invoice or bill the user needs to pay, reply with ONLY "
    'JSON: {"is_invoice": true, "payee": str, "amount": str, "due_date": str, '
    '"invoice_number": str, "sort_code": str, "account_number": str}. '
    "Use empty strings for fields you cannot find. If it is NOT a payable invoice "
    '(newsletter, receipt for something already paid, marketing, etc.), reply '
    '{"is_invoice": false}.'
)

_FIELDS = ("payee", "amount", "due_date", "invoice_number", "sort_code", "account_number")


def _parse(content: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", (content or "").strip(), flags=re.MULTILINE).strip()
    start = text.find("{")
    if start == -1:
        return {}
    try:
        data = json.loads(text[start:])
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _has_bank_details(data: dict) -> bool:
    return bool(data.get("sort_code") and data.get("account_number"))


class InvoiceExtractionAgent(SubAgent):
    async def extract(self, subject: str, body: str) -> dict:
        reply = await self._reply(_INVOICE_SYSTEM, f"Subject: {subject}\n\n{body}")
        return _parse(reply)


class InvoiceExtractorTask:
    def __init__(self, accounts, client, model: str, connector, store, chat_id: int):
        self._accounts = accounts
        self._client = client
        self._model = model
        self._connector = connector
        self._store = store
        self._chat_id = chat_id

    async def run(self, context) -> None:
        for label, gmail in self._accounts:
            try:
                await self._check_account(label, gmail)
            except Exception:
                logger.exception("Invoice extraction failed for account %s", label)

    async def _check_account(self, label: str, gmail) -> None:
        emails = gmail.get_unread()
        if not emails:
            return
        by_key = {f"inv:{label}:{e['id']}": e for e in emails}
        new_keys = self._store.filter_unseen(list(by_key))
        if not new_keys:
            return

        agent = InvoiceExtractionAgent(self._client, self._model)
        for key in new_keys:
            email = by_key[key]
            try:
                data = await self._extract_invoice(agent, gmail, email)
                if data.get("is_invoice"):
                    await self._announce(data)
            finally:
                self._store.mark_seen([key])

    async def _extract_invoice(self, agent, gmail, email) -> dict:
        body = gmail.get_full_text(email["id"])
        data = await agent.extract(email["subject"], body)
        # Only crack open the PDF when it's an invoice but the bank details are
        # missing from the body — keeps PDF parsing to when it's actually needed.
        if data.get("is_invoice") and not _has_bank_details(data):
            pdf_text = gmail.get_pdf_text(email["id"])
            if pdf_text:
                enriched = await agent.extract(email["subject"], f"{body}\n\n{pdf_text}")
                if enriched.get("is_invoice"):
                    data = enriched
        return data

    async def _announce(self, data: dict) -> None:
        lines = [f"Invoice to pay: {data.get('payee') or 'unknown payee'}"]
        labels = {
            "amount": "Amount", "due_date": "Due", "invoice_number": "Invoice no",
            "sort_code": "Sort code", "account_number": "Account",
        }
        for field, label in labels.items():
            if data.get(field):
                lines.append(f"{label}: {data[field]}")
        if not _has_bank_details(data):
            lines.append("(Could not find full bank details — check the email/attachment.)")
        text = "\n".join(lines)
        logger.info("Invoice: %s", data.get("payee"))
        await self._connector.send_message(self._chat_id, text)
