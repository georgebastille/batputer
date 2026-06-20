"""Google Calendar access for the primary account.

Read side (this phase): list events and check whether an extracted event already
exists, so we only ask the user about genuinely new ones. Built on the same
get_google_service auth as Gmail.
"""
import datetime
import re

TIMEZONE = "Europe/London"
# Every event BatPuter creates is prefixed so it's identifiable in the calendar
# (and so dedup can recognise its own prior events). Lowercased prefix tokens are
# ignored when matching titles.
EVENT_PREFIX = "RSM: "
_PREFIX_TOKENS = {"rsm"}


def _event_body(event: dict) -> dict:
    """Translate an extracted event ({title,date,start,end,location}) into a Google
    Calendar insert body. Timed when a start time is present (end defaults to +1h);
    otherwise an all-day event (Google's end date is exclusive, so it's the next day)."""
    date = event["date"]
    body = {"summary": EVENT_PREFIX + event["title"]}
    if event.get("location"):
        body["location"] = event["location"]
    description = "\n".join(filter(None, [
        f"Year groups: {event['year_groups']}" if event.get("year_groups") else "",
        event.get("notes", ""),
    ]))
    if description:
        body["description"] = description
    start = (event.get("start") or "").strip()
    if start:
        end = (event.get("end") or "").strip() or _plus_hour(start)
        body["start"] = {"dateTime": f"{date}T{_hhmm(start)}:00", "timeZone": TIMEZONE}
        body["end"] = {"dateTime": f"{date}T{_hhmm(end)}:00", "timeZone": TIMEZONE}
    else:
        next_day = (datetime.date.fromisoformat(date) + datetime.timedelta(days=1)).isoformat()
        body["start"] = {"date": date}
        body["end"] = {"date": next_day}
    return body


def _hhmm(value: str) -> str:
    h, _, m = value.strip().partition(":")
    return f"{int(h):02d}:{int(m or 0):02d}"


def _plus_hour(value: str) -> str:
    h, _, m = value.strip().partition(":")
    return f"{(int(h) + 1) % 24:02d}:{int(m or 0):02d}"


def _tokens(text: str) -> set[str]:
    """Significant word tokens of a title, ignoring punctuation, order, and the
    RSM prefix — robust to reordering and extra qualifiers."""
    words = re.sub(r"[^a-z0-9 ]+", "", (text or "").lower()).split()
    return set(words) - _PREFIX_TOKENS


def _start_value(event: dict) -> str:
    start = event.get("start", {})
    return start.get("dateTime") or start.get("date") or ""


class CalendarClient:
    def __init__(self, service):
        self._service = service

    def list_events(self, time_min: str, time_max: str, calendar_id: str = "primary") -> list[dict]:
        """Events between two RFC3339 timestamps, soonest first. Recurring events
        are expanded (singleEvents)."""
        result = (
            self._service.events()
            .list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        return [
            {"id": e.get("id"), "summary": e.get("summary", ""), "start": _start_value(e)}
            for e in result.get("items", [])
        ]

    def find_matching(self, title: str, date: str, calendar_id: str = "primary") -> dict | None:
        """Return an existing event on `date` (YYYY-MM-DD) whose title matches
        `title` by token set (order-independent, ignores the RSM prefix), else
        None — used to skip events already in the calendar. A match is when one
        title's significant words are a subset of the other's."""
        target = _tokens(title)
        if not target:
            return None
        for event in self.list_events(f"{date}T00:00:00Z", f"{date}T23:59:59Z", calendar_id):
            summary = _tokens(event["summary"])
            if summary and (target <= summary or summary <= target):
                return event
        return None

    def create_event(self, event: dict, calendar_id: str = "primary") -> dict:
        """Insert an extracted event into the calendar. Returns the created event."""
        return (
            self._service.events()
            .insert(calendarId=calendar_id, body=_event_body(event))
            .execute()
        )
