import logging
from typing import Awaitable, Callable

from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

logger = logging.getLogger(__name__)


class TelegramConnector:
    def __init__(self, token: str, message_handler: Callable[[int, str], Awaitable[str]]):
        self._app = ApplicationBuilder().token(token).build()
        self._message_handler = message_handler

    def run(self) -> None:
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message)
        )
        self._app.run_polling()

    @property
    def app(self):
        return self._app

    async def send_message(self, chat_id: int, text: str) -> None:
        await self._app.bot.send_message(chat_id=chat_id, text=text)

    async def send_typing(self, chat_id: int) -> None:
        await self._app.bot.send_chat_action(chat_id=chat_id, action="typing")

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        try:
            await self.send_typing(chat_id)
            reply = await self._message_handler(chat_id, update.message.text)
            await self.send_message(chat_id, reply)
        except RuntimeError as e:
            await self.send_message(chat_id, str(e))
        except Exception:
            logger.exception("Unhandled error in message handler for chat_id=%s", chat_id)
            await self.send_message(chat_id, "Something went wrong. Please try again.")
