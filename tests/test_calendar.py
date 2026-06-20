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


def test_find_matching_ignores_order_and_rsm_prefix():
    # A previously-created "RSM: Gurdwara Trip Year 4" must match an extracted
    # "Year 4 Gurdwara Trip" (reordered, prefixed) so we don't duplicate it.
    service = _service_returning([
        {"id": "x", "summary": "RSM: Gurdwara Trip Year 4", "start": {"date": "2026-06-24"}},
    ])
    client = CalendarClient(service)
    assert client.find_matching("Year 4 Gurdwara Trip", "2026-06-24")["id"] == "x"


def test_find_matching_distinct_events_not_merged():
    service = _service_returning([
        {"id": "x", "summary": "RSM: Year 4 Trip to the Zoo", "start": {"date": "2026-06-24"}},
    ])
    client = CalendarClient(service)
    assert client.find_matching("Year 4 Trip to the Museum", "2026-06-24") is None


def test_find_matching_none_when_no_similar_event():
    service = _service_returning([
        {"id": "x", "summary": "Dentist appointment", "start": {"date": "2026-07-01"}},
    ])
    client = CalendarClient(service)

    assert client.find_matching("Sports Day", "2026-07-01") is None


def test_find_matching_empty_title_is_none():
    client = CalendarClient(_service_returning([]))
    assert client.find_matching("", "2026-07-01") is None


def test_create_timed_event_body():
    service = MagicMock()
    service.events().insert().execute.return_value = {"id": "new"}
    client = CalendarClient(service)

    client.create_event({"title": "Trip", "date": "2026-06-24", "start": "10:30",
                         "end": "12:00", "location": "Croydon"})

    body = service.events().insert.call_args.kwargs["body"]
    assert body["summary"] == "RSM: Trip"  # all BatPuter events are RSM-prefixed
    assert body["location"] == "Croydon"
    assert body["start"]["dateTime"] == "2026-06-24T10:30:00"
    assert body["end"]["dateTime"] == "2026-06-24T12:00:00"


def test_create_event_description_has_year_groups_and_notes():
    service = MagicMock()
    service.events().insert().execute.return_value = {"id": "new"}
    client = CalendarClient(service)

    client.create_event({"title": "Sports Day", "date": "2026-07-01", "start": "", "end": "",
                         "year_groups": "Reception", "notes": "Bring sun cream and a hat."})

    desc = service.events().insert.call_args.kwargs["body"]["description"]
    assert "Reception" in desc and "sun cream" in desc


def test_create_all_day_event_uses_exclusive_end_date():
    service = MagicMock()
    service.events().insert().execute.return_value = {"id": "new"}
    client = CalendarClient(service)

    client.create_event({"title": "Mufti Day", "date": "2026-07-01", "start": "", "end": ""})

    body = service.events().insert.call_args.kwargs["body"]
    assert body["start"] == {"date": "2026-07-01"}
    assert body["end"] == {"date": "2026-07-02"}  # Google end date is exclusive


def test_create_timed_event_defaults_end_to_plus_one_hour():
    service = MagicMock()
    service.events().insert().execute.return_value = {"id": "new"}
    client = CalendarClient(service)

    client.create_event({"title": "Assembly", "date": "2026-07-01", "start": "9:00", "end": ""})

    body = service.events().insert.call_args.kwargs["body"]
    assert body["start"]["dateTime"] == "2026-07-01T09:00:00"
    assert body["end"]["dateTime"] == "2026-07-01T10:00:00"
