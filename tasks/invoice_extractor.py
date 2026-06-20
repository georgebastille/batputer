"""Surface invoices/bills with the details needed to pay them.

Per scheduled run, for each Gmail account: for new unread mail, ask the model
whether it's a payable invoice and extract the payee, amount, due date, invoice
number, and bank details (sort code + account number). If the bank details aren't
in the email body, fall back to extracting text from any PDF attachment and try
again. Matching invoices are announced on Telegram with everything needed to pay.
"""
import logging

from tasks.email_task import PerAccountEmailTask
from tools.commons import SubAgent, parse_json_object

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


def _has_bank_details(data: dict) -> bool:
    return bool(data.get("sort_code") and data.get("account_number"))


class InvoiceExtractionAgent(SubAgent):
    async def extract(self, subject: str, body: str) -> dict:
        reply = await self._reply(_INVOICE_SYSTEM, f"Subject: {subject}\n\n{body}")
        return parse_json_object(reply)


class InvoiceExtractorTask(PerAccountEmailTask):
    seen_prefix = "inv:"  # separate dedup namespace from triage/event tasks

    def __init__(self, accounts, client, model: str, connector, store, chat_id: int):
        super().__init__(accounts, store)
        self._client = client
        self._model = model
        self._connector = connector
        self._chat_id = chat_id

    async def check_account(self, label, gmail) -> None:
        new = self.new_unseen(label, gmail)
        if not new:
            return
        agent = InvoiceExtractionAgent(self._client, self._model)
        for key, email in new:
            try:
                data = await self._extract_invoice(agent, gmail, email)
                if data.get("is_invoice"):
                    await self._announce(data)
            finally:
                self.mark_seen([key])

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
