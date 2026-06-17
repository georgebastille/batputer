"""Extract Rosemead school events (Reception / Year 4) from email and surface the
new ones.

Per scheduled run, for each Gmail account: take unread mail from the school
sender, extract any events affecting the configured year groups, drop ones
already in the calendar, and notify about the rest. The interactive [Add]/[Skip]
confirmation + calendar write are added in the next phase; for now new events are
logged and announced as plain messages.
"""
import asyncio
import datetime
import json
import logging
import re
import zoneinfo
from typing import TYPE_CHECKING

from tools.commons import SubAgent

if TYPE_CHECKING:
    from connectors.calendar import CalendarClient

logger = logging.getLogger(__name__)
LONDON_TZ = zoneinfo.ZoneInfo("Europe/London")


def _extract_system(year_groups: str, today: str) -> str:
    return (
        "You read emails from a primary school and extract calendar events that "
        f"affect these year groups: {year_groups}. Today is {today}. "
        "Include whole-school events; ignore events only for other year groups, and "
        "ignore non-events (newsletters, reminders with no date, general info). "
        "Resolve relative dates (e.g. 'next Friday') to absolute dates using today's date. "
        'Reply with ONLY JSON: {"events": [{"title": str, "date": "YYYY-MM-DD", '
        '"start": "HH:MM or empty", "end": "HH:MM or empty", "location": str, '
        '"year_groups": str}]}. If there are no relevant events, reply {"events": []}.'
    )


def _parse_events(content: str) -> list[dict]:
    """Robustly pull the events array from the model's reply (tolerates code fences)."""
    text = (content or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start = text.find("{")
    if start == -1:
        return []
    try:
        data = json.loads(text[start:])
    except (json.JSONDecodeError, ValueError):
        return []
    events = data.get("events", []) if isinstance(data, dict) else []
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


class EventExtractorTask:
    def __init__(self, accounts, calendar: "CalendarClient", client, model: str,
                 connector, store, chat_id: int, school_sender: str, year_groups: str):
        self._accounts = accounts
        self._calendar = calendar
        self._client = client
        self._model = model
        self._connector = connector
        self._store = store
        self._chat_id = chat_id
        self._school_sender = school_sender.lower()
        self._year_groups = year_groups

    async def run(self, context) -> None:
        if not self._calendar:
            return
        for label, gmail in self._accounts:
            try:
                await self._check_account(label, gmail)
            except Exception:
                logger.exception("Event extraction failed for account %s", label)

    async def _check_account(self, label: str, gmail) -> None:
        school = [e for e in gmail.get_unread() if self._school_sender in e["from"].lower()]
        if not school:
            return
        # Separate "seen" namespace from the triage monitor so they don't clobber each other.
        by_key = {f"evt:{label}:{e['id']}": e for e in school}
        new_keys = self._store.filter_unseen(list(by_key))
        if not new_keys:
            return

        agent = EventExtractionAgent(self._client, self._model, self._year_groups)
        for key in new_keys:
            email = by_key[key]
            try:
                body = gmail.get_full_text(email["id"])
                events = await agent.extract(email["subject"], body)
                for event in events:
                    if self._calendar.find_matching(event["title"], event["date"]):
                        logger.info("School event already in calendar: %s", event["title"])
                        continue
                    await self._announce(event)
            finally:
                self._store.mark_seen([key])

    async def _announce(self, event: dict) -> None:
        when = event["date"] + (f" {event['start']}" if event.get("start") else "")
        where = f" @ {event['location']}" if event.get("location") else ""
        text = f"New school event: {event['title']} — {when}{where} ({event.get('year_groups', '')})"
        logger.info(text)
        await self._connector.send_message(self._chat_id, text)
