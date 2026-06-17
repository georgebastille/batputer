from unittest.mock import MagicMock

from connectors.calendar import CalendarClient


def _service_returning(items):
    service = MagicMock()
    service.events().list().execute.return_value = {"items": items}
    return service


def test_list_events_simplifies_items():
    service = _service_returning([
        {"id": "a", "summary": "Sports Day", "start": {"dateTime": "2026-07-01T09:00:00Z"}},
        {"id": "b", "summary": "All-day thing", "start": {"date": "2026-07-02"}},
    ])
    client = CalendarClient(service)

    events = client.list_events("2026-07-01T00:00:00Z", "2026-07-03T00:00:00Z")
    assert events == [
        {"id": "a", "summary": "Sports Day", "start": "2026-07-01T09:00:00Z"},
        {"id": "b", "summary": "All-day thing", "start": "2026-07-02"},
    ]


def test_find_matching_returns_event_with_similar_title():
    service = _service_returning([
        {"id": "x", "summary": "Reception Sports Day!", "start": {"date": "2026-07-01"}},
    ])
    client = CalendarClient(service)

    match = client.find_matching("Sports Day", "2026-07-01")
    assert match is not None and match["id"] == "x"


def test_find_matching_none_when_no_similar_event():
    service = _service_returning([
        {"id": "x", "summary": "Dentist appointment", "start": {"date": "2026-07-01"}},
    ])
    client = CalendarClient(service)

    assert client.find_matching("Sports Day", "2026-07-01") is None


def test_find_matching_empty_title_is_none():
    client = CalendarClient(_service_returning([]))
    assert client.find_matching("", "2026-07-01") is None
