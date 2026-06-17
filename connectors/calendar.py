"""Google Calendar access for the primary account.

Read side (this phase): list events and check whether an extracted event already
exists, so we only ask the user about genuinely new ones. Built on the same
get_google_service auth as Gmail.
"""
import datetime
import re

TIMEZONE = "Europe/London"


def _event_body(event: dict) -> dict:
    """Translate an extracted event ({title,date,start,end,location}) into a Google
    Calendar insert body. Timed when a start time is present (end defaults to +1h);
    otherwise an all-day event (Google's end date is exclusive, so it's the next day)."""
    date = event["date"]
    body = {"summary": event["title"]}
    if event.get("location"):
        body["location"] = event["location"]
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


def _norm(text: str) -> str:
    """Lowercased, punctuation-stripped form for fuzzy title comparison."""
    return re.sub(r"[^a-z0-9 ]+", "", (text or "").lower()).strip()


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
        """Return an existing event on `date` (YYYY-MM-DD) whose title fuzzily
        matches `title`, else None — used to skip events already in the calendar."""
        time_min = f"{date}T00:00:00Z"
        time_max = f"{date}T23:59:59Z"
        target = _norm(title)
        if not target:
            return None
        for event in self.list_events(time_min, time_max, calendar_id):
            summary = _norm(event["summary"])
            if summary and (target in summary or summary in target):
                return event
        return None

    def create_event(self, event: dict, calendar_id: str = "primary") -> dict:
        """Insert an extracted event into the calendar. Returns the created event."""
        return (
            self._service.events()
            .insert(calendarId=calendar_id, body=_event_body(event))
            .execute()
        )
