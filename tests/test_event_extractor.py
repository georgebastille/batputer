import asyncio
from unittest.mock import AsyncMock, MagicMock

from llm.mlx_client import ChatResult
from persistence.store import ConversationStore
from tasks.event_extractor import EventExtractorTask, _parse_events


def _events_json(*events):
    import json
    return json.dumps({"events": list(events)})


SPORTS_DAY = {"title": "Reception Sports Day", "date": "2026-07-01", "start": "09:30",
              "end": "", "location": "Field", "year_groups": "Reception"}


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


def test_new_school_event_is_announced():
    gmail = _gmail([{"id": "1", "from": "office@rosemead.sch.uk", "subject": "Sports Day"}])
    calendar = MagicMock()
    calendar.find_matching.return_value = None  # not already in calendar
    connector = MagicMock(); connector.send_message = AsyncMock()
    store = ConversationStore(":memory:")

    task = _task(gmail, calendar, _client(_events_json(SPORTS_DAY)), connector, store)
    asyncio.run(task.run(None))

    connector.send_message.assert_called_once()
    assert "Reception Sports Day" in connector.send_message.call_args.args[1]
    assert store.filter_unseen(["evt:primary:1"]) == []  # marked seen


def test_event_already_in_calendar_is_skipped():
    gmail = _gmail([{"id": "1", "from": "office@rosemead.sch.uk", "subject": "Sports Day"}])
    calendar = MagicMock()
    calendar.find_matching.return_value = {"id": "x", "summary": "Reception Sports Day"}
    connector = MagicMock(); connector.send_message = AsyncMock()
    store = ConversationStore(":memory:")

    task = _task(gmail, calendar, _client(_events_json(SPORTS_DAY)), connector, store)
    asyncio.run(task.run(None))

    connector.send_message.assert_not_called()


def test_non_school_email_is_ignored():
    gmail = _gmail([{"id": "1", "from": "newsletter@shop.com", "subject": "Sale"}])
    calendar = MagicMock()
    client = _client(_events_json(SPORTS_DAY))
    connector = MagicMock(); connector.send_message = AsyncMock()
    store = ConversationStore(":memory:")

    task = _task(gmail, calendar, client, connector, store)
    asyncio.run(task.run(None))

    client.generate.assert_not_called()  # never even ran extraction
    connector.send_message.assert_not_called()


def test_no_events_extracted_no_announcement():
    gmail = _gmail([{"id": "1", "from": "office@rosemead.sch.uk", "subject": "Newsletter"}])
    calendar = MagicMock()
    connector = MagicMock(); connector.send_message = AsyncMock()
    store = ConversationStore(":memory:")

    task = _task(gmail, calendar, _client('{"events": []}'), connector, store)
    asyncio.run(task.run(None))

    connector.send_message.assert_not_called()
    assert store.filter_unseen(["evt:primary:1"]) == []  # still marked seen


def test_no_calendar_is_noop():
    gmail = _gmail([{"id": "1", "from": "office@rosemead.sch.uk", "subject": "Sports Day"}])
    client = _client(_events_json(SPORTS_DAY))
    connector = MagicMock(); connector.send_message = AsyncMock()
    task = _task(gmail, None, client, connector, ConversationStore(":memory:"))
    asyncio.run(task.run(None))
    client.generate.assert_not_called()
