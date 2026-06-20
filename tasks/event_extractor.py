"""Extract Rosemead school events (Reception / Year 4) from email and surface the
new ones.

Per scheduled run, for each Gmail account: take unread mail from the school
sender, extract any events affecting the configured year groups, drop ones
already in the calendar, and notify about the rest. The interactive [Add]/[Skip]
confirmation + calendar write are added in the next phase; for now new events are
logged and announced as plain messages.
"""
import datetime
import logging
import uuid
import zoneinfo

from tasks.email_task import PerAccountEmailTask
from tools.commons import SubAgent, parse_json_object

logger = logging.getLogger(__name__)
LONDON_TZ = zoneinfo.ZoneInfo("Europe/London")


def _extract_system(year_groups: str, today: str) -> str:
    return (
        "You read emails from a primary school and extract calendar events that "
        f"affect these year groups: {year_groups}. Today is {today}. "
        "Include whole-school events; ignore events only for other year groups, and "
        "ignore non-events (newsletters, reminders with no date, general info). "
        "Resolve relative dates (e.g. 'next Friday') to absolute dates using today's date. "
        "Capture practical details a parent needs in 'notes': what to bring, dress code "
        "(e.g. fancy dress / non-uniform / kit), money, permission slips, and anything else "
        "to be aware of. "
        'Reply with ONLY JSON: {"events": [{"title": str, "date": "YYYY-MM-DD", '
        '"start": "HH:MM or empty", "end": "HH:MM or empty", "location": str, '
        '"year_groups": str, "notes": "practical details or empty"}]}. '
        'If there are no relevant events, reply {"events": []}.'
    )


def _parse_events(content: str) -> list[dict]:
    """Pull the valid events (must have a title and date) from the model's reply."""
    events = parse_json_object(content).get("events", [])
    return [e for e in events if isinstance(e, dict) and e.get("title") and e.get("date")]


class EventExtractionAgent(SubAgent):
    def __init__(self, client, model: str, year_groups: str):
        super().__init__(client, model)
        self._year_groups = year_groups

    async def extract(self, subject: str, body: str) -> list[dict]:
        today = datetime.datetime.now(LONDON_TZ).strftime("%Y-%m-%d (%A)")
        reply = await self._reply(
            _extract_system(self._year_groups, today),
            f"Subject: {subject}\n\n{body}",
        )
        return _parse_events(reply)


class EventExtractorTask(PerAccountEmailTask):
    seen_prefix = "evt:"  # separate dedup namespace from triage/invoice tasks

    def __init__(self, accounts, calendar, client, model: str,
                 connector, store, chat_id: int, school_sender: str, year_groups: str):
        super().__init__(accounts, store)
        self._calendar = calendar
        self._client = client
        self._model = model
        self._connector = connector
        self._chat_id = chat_id
        self._school_sender = school_sender.lower()
        self._year_groups = year_groups

    async def run(self, context) -> None:
        if self._calendar:
            await super().run(context)

    def _is_school(self, email: dict) -> bool:
        return self._school_sender in email["from"].lower()

    async def check_account(self, label, gmail) -> None:
        new = self.new_unseen(label, gmail, predicate=self._is_school)
        if not new:
            return
        agent = EventExtractionAgent(self._client, self._model, self._year_groups)
        for key, email in new:
            try:
                for event in await agent.extract(email["subject"], gmail.get_full_text(email["id"])):
                    if self._calendar.find_matching(event["title"], event["date"]):
                        logger.info("School event already in calendar: %s", event["title"])
                        continue
                    await self._announce(event)
            finally:
                self.mark_seen([key])

    async def _announce(self, event: dict) -> None:
        when = event["date"] + (f" {event['start']}" if event.get("start") else "")
        where = f" @ {event['location']}" if event.get("location") else ""
        notes = f"\nNotes: {event['notes']}" if event.get("notes") else ""
        text = (
            f"New school event: {event['title']} — {when}{where} "
            f"({event.get('year_groups', '')}).{notes}\nAdd to your calendar?"
        )
        logger.info(text)
        token = uuid.uuid4().hex[:10]
        self._store.add_pending(token, event)
        await self._connector.send_confirmation(self._chat_id, text, token)
