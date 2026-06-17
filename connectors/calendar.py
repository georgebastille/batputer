"""Google Calendar access for the primary account.

Read side (this phase): list events and check whether an extracted event already
exists, so we only ask the user about genuinely new ones. Built on the same
get_google_service auth as Gmail.
"""
import re


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
