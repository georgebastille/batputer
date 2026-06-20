import asyncio
from unittest.mock import AsyncMock, MagicMock

from llm.mlx_client import ChatResult
from persistence.store import ConversationStore
from tasks.event_extractor import EventExtractorTask, _parse_events


def _events_json(*events):
    import json
    return json.dumps({"events": list(events)})


SPORTS_DAY = {"title": "Reception Sports Day", "date": "2026-07-01", "start": "09:30",
              "end": "", "location": "Field", "year_groups": "Reception",
              "notes": "Bring a hat and water bottle."}


def _gmail(emails, body="Sports day on 1 July at 9:30 for Reception."):
    g = MagicMock()
    g.get_unread.return_value = emails
    g.get_full_text.return_value = body
    return g


def _task(gmail, calendar, client, connector, store, sender="rosemead"):
    return EventExtractorTask(
        accounts=[("primary", gmail)], calendar=calendar, client=client, model="m",
        connector=connector, store=store, chat_id=42,
        school_sender=sender, year_groups="Reception, Year 4",
    )


def _client(content):
    c = MagicMock()
    c.generate.return_value = ChatResult(content=content)
    return c


def test_parse_events_tolerates_code_fences():
    assert _parse_events('```json\n{"events": [{"title":"X","date":"2026-07-01"}]}\n```') == [
        {"title": "X", "date": "2026-07-01"}
    ]
    assert _parse_events("sorry, no json here") == []


def _connector():
    c = MagicMock()
    c.send_confirmation = AsyncMock()
    return c


def test_new_school_event_offers_confirmation():
    gmail = _gmail([{"id": "1", "from": "office@rosemead.sch.uk", "subject": "Sports Day"}])
    calendar = MagicMock()
    calendar.find_matching.return_value = None  # not already in calendar
    connector = _connector()
    store = ConversationStore(":memory:")

    task = _task(gmail, calendar, _client(_events_json(SPORTS_DAY)), connector, store)
    asyncio.run(task.run(None))

    connector.send_confirmation.assert_called_once()
    chat_id, text, token = connector.send_confirmation.call_args.args
    assert "Reception Sports Day" in text
    assert "Bring a hat" in text  # practical notes surfaced in the prompt
    stored = store.pop_pending(token)
    assert stored["title"] == "Reception Sports Day"  # event was stored
    assert stored["notes"] == "Bring a hat and water bottle."
    assert store.filter_unseen(["evt:primary:1"]) == []  # marked seen


def test_event_already_in_calendar_is_skipped():
    gmail = _gmail([{"id": "1", "from": "office@rosemead.sch.uk", "subject": "Sports Day"}])
    calendar = MagicMock()
    calendar.find_matching.return_value = {"id": "x", "summary": "Reception Sports Day"}
    connector = _connector()
    store = ConversationStore(":memory:")

    task = _task(gmail, calendar, _client(_events_json(SPORTS_DAY)), connector, store)
    asyncio.run(task.run(None))

    connector.send_confirmation.assert_not_called()


def test_non_school_email_is_ignored():
    gmail = _gmail([{"id": "1", "from": "newsletter@shop.com", "subject": "Sale"}])
    calendar = MagicMock()
    client = _client(_events_json(SPORTS_DAY))
    connector = _connector()
    store = ConversationStore(":memory:")

    task = _task(gmail, calendar, client, connector, store)
    asyncio.run(task.run(None))

    client.generate.assert_not_called()  # never even ran extraction
    connector.send_confirmation.assert_not_called()


def test_no_events_extracted_no_confirmation():
    gmail = _gmail([{"id": "1", "from": "office@rosemead.sch.uk", "subject": "Newsletter"}])
    calendar = MagicMock()
    connector = _connector()
    store = ConversationStore(":memory:")

    task = _task(gmail, calendar, _client('{"events": []}'), connector, store)
    asyncio.run(task.run(None))

    connector.send_confirmation.assert_not_called()
    assert store.filter_unseen(["evt:primary:1"]) == []  # still marked seen


def test_no_calendar_is_noop():
    gmail = _gmail([{"id": "1", "from": "office@rosemead.sch.uk", "subject": "Sports Day"}])
    client = _client(_events_json(SPORTS_DAY))
    task = _task(gmail, None, client, _connector(), ConversationStore(":memory:"))
    asyncio.run(task.run(None))
    client.generate.assert_not_called()
