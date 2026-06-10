import asyncio
from unittest.mock import AsyncMock, MagicMock

import telegram

from connectors.telegram import TelegramConnector, _StatusReporter
from tools.commons import Result, Status


def _make_bot():
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    bot.edit_message_text = AsyncMock()
    bot.delete_message = AsyncMock()
    bot.send_chat_action = AsyncMock()
    return bot


def test_status_reporter_create_then_edit_then_clear():
    bot = _make_bot()
    status = _StatusReporter(bot, chat_id=1)

    asyncio.run(status.update("first"))
    bot.send_message.assert_called_once_with(chat_id=1, text="first")

    asyncio.run(status.update("second"))
    bot.edit_message_text.assert_called_once_with(chat_id=1, message_id=42, text="second")

    asyncio.run(status.clear())
    bot.delete_message.assert_called_once_with(chat_id=1, message_id=42)


def test_status_reporter_swallows_bad_request():
    bot = _make_bot()
    bot.edit_message_text.side_effect = telegram.error.BadRequest("message is not modified")
    bot.delete_message.side_effect = telegram.error.BadRequest("message to delete not found")
    status = _StatusReporter(bot, chat_id=1)

    asyncio.run(status.update("first"))
    asyncio.run(status.update("second"))  # should not raise
    asyncio.run(status.clear())  # should not raise


def _make_connector(message_handler):
    connector = TelegramConnector.__new__(TelegramConnector)
    connector._app = MagicMock()
    connector._app.bot = _make_bot()
    connector._message_handler = message_handler
    return connector


def _make_update(text="hello"):
    update = MagicMock()
    update.effective_chat.id = 1
    update.message.text = text
    return update


def _make_photo_update(caption=""):
    update = MagicMock()
    update.effective_chat.id = 1
    update.message.caption = caption
    update.message.photo = [MagicMock(file_id="small"), MagicMock(file_id="large")]
    return update


def test_on_message_status_then_result():
    async def handler(chat_id, text, image_data_url=None):
        yield Status("a")
        yield Status("b")
        yield Result("done")

    connector = _make_connector(handler)
    asyncio.run(connector._on_message(_make_update(), MagicMock()))

    bot = connector._app.bot
    assert bot.send_message.call_count == 2  # one status message, one final reply
    bot.edit_message_text.assert_called_once()  # second status update
    bot.delete_message.assert_called_once()  # status cleared before final reply


def test_on_message_no_status_for_plain_reply():
    async def handler(chat_id, text, image_data_url=None):
        yield Result("just a reply")

    connector = _make_connector(handler)
    asyncio.run(connector._on_message(_make_update(), MagicMock()))

    bot = connector._app.bot
    bot.send_message.assert_called_once_with(chat_id=1, text="just a reply")
    bot.edit_message_text.assert_not_called()
    bot.delete_message.assert_not_called()


def test_on_photo_passes_image_data_url_and_caption():
    received = {}

    async def handler(chat_id, text, image_data_url=None):
        received["text"] = text
        received["image_data_url"] = image_data_url
        yield Result("I see a cat")

    connector = _make_connector(handler)
    context = MagicMock()
    fake_file = MagicMock()
    fake_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"fake-image-bytes"))
    context.bot.get_file = AsyncMock(return_value=fake_file)

    asyncio.run(connector._on_photo(_make_photo_update(caption="what is this?"), context))

    context.bot.get_file.assert_called_once_with("large")
    assert received["text"] == "what is this?"
    assert received["image_data_url"].startswith("data:image/jpeg;base64,")
    connector._app.bot.send_message.assert_called_once_with(chat_id=1, text="I see a cat")


def test_on_message_runtime_error_clears_status():
    async def handler(chat_id, text, image_data_url=None):
        yield Status("working...")
        raise RuntimeError("Cannot reach the local LLM. Is LM Studio running?")

    connector = _make_connector(handler)
    asyncio.run(connector._on_message(_make_update(), MagicMock()))

    bot = connector._app.bot
    bot.delete_message.assert_called_once()
    bot.send_message.assert_called_with(chat_id=1, text="Cannot reach the local LLM. Is LM Studio running?")
