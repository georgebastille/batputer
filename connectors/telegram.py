import base64
import logging
import re
from typing import AsyncIterator, Callable, Optional

import telegram
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

from tools.commons import Result, Status

logger = logging.getLogger(__name__)


def _strip_markdown(text: str) -> str:
    text = re.sub(r'\|\|(.*?)\|\|', r'\1', text)          # ||spoiler||
    text = re.sub(r'\*{1,3}(.*?)\*{1,3}', r'\1', text)    # ***bold italic*** / **bold** / *italic*
    text = re.sub(r'_{1,3}(.*?)_{1,3}', r'\1', text)       # __underline__ / _italic_
    text = re.sub(r'~~(.*?)~~', r'\1', text)               # ~~strikethrough~~
    text = re.sub(r'`{1,3}(.*?)`{1,3}', r'\1', text, flags=re.DOTALL)  # `code` / ```block```
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)          # # headers
    return text


class _StatusReporter:
    def __init__(self, bot, chat_id: int):
        self._bot = bot
        self._chat_id = chat_id
        self._message_id = None

    async def update(self, text: str) -> None:
        if self._message_id is None:
            msg = await self._bot.send_message(chat_id=self._chat_id, text=text)
            self._message_id = msg.message_id
        else:
            try:
                await self._bot.edit_message_text(
                    chat_id=self._chat_id, message_id=self._message_id, text=text
                )
            except telegram.error.BadRequest:
                pass

    async def clear(self) -> None:
        if self._message_id is not None:
            try:
                await self._bot.delete_message(chat_id=self._chat_id, message_id=self._message_id)
            except telegram.error.BadRequest:
                pass
            self._message_id = None


class TelegramConnector:
    def __init__(
        self,
        token: str,
        message_handler: Callable[[int, str, Optional[str]], AsyncIterator[Status | Result]],
    ):
        self._app = ApplicationBuilder().token(token).build()
        self._message_handler = message_handler

    def run(self) -> None:
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message)
        )
        self._app.add_handler(MessageHandler(filters.PHOTO, self._on_photo))
        self._app.run_polling()

    @property
    def app(self):
        return self._app

    async def send_message(self, chat_id: int, text: str) -> None:
        await self._app.bot.send_message(chat_id=chat_id, text=text)

    async def send_typing(self, chat_id: int) -> None:
        await self._app.bot.send_chat_action(chat_id=chat_id, action="typing")

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._handle(update, update.message.text)

    async def _on_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        photo = update.message.photo[-1]  # largest available size
        file = await context.bot.get_file(photo.file_id)
        image_bytes = bytes(await file.download_as_bytearray())
        image_data_url = f"data:image/jpeg;base64,{base64.b64encode(image_bytes).decode()}"
        await self._handle(update, update.message.caption or "", image_data_url)

    async def _handle(self, update: Update, text: str, image_data_url: Optional[str] = None) -> None:
        chat_id = update.effective_chat.id
        status = _StatusReporter(self._app.bot, chat_id)
        try:
            await self.send_typing(chat_id)
            async for item in self._message_handler(chat_id, text, image_data_url):
                if isinstance(item, Result):
                    await status.clear()
                    await self.send_message(chat_id, _strip_markdown(item.text))
                else:
                    await status.update(item.text)
        except RuntimeError as e:
            await status.clear()
            await self.send_message(chat_id, str(e))
        except Exception:
            logger.exception("Unhandled error in message handler for chat_id=%s", chat_id)
            await status.clear()
            await self.send_message(chat_id, "Something went wrong. Please try again.")
