import os
import sys
from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from telegram import Update
from openai import OpenAI


class BatPuter:
    def __init__(self, telegram_app, openai_client) -> None:
        self.telegram = telegram_app
        self.openai = openai_client
        self.messages = [
            {
                "role": "system",
                "content": "You are a helpful assistant. You respond to the user with a witty joke based on what they said.",
            }
        ]

    async def _handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        user_message = {"role": "user", "content": update.message.text}
        self.messages.append(user_message)

        response = self.openai.chat.completions.create(
            model=MODEL,
            messages=self.messages,
            extra_body={"thinking": {"type": "disabled"}},
        )
        reply = response.choices[0].message.content
        self.messages.append({"role": "assistant", "content": reply})

        await context.bot.send_message(chat_id=chat_id, text=reply)

    def run(self):
        self.telegram.add_handler(MessageHandler(filters.TEXT, self._handle))
        self.telegram.run_polling()


if __name__ == "__main__":
    load_dotenv()
    client = OpenAI(base_url="http://192.168.50.132:1234/v1", api_key="lm-studio")
    MODEL = "gemma-4-26b-a4b-it-mlx"
    model_ids = [model.id for model in client.models.list()]
    if MODEL not in model_ids:
        # if requested model is not in the list, log an error
        print("Model not found")
        sys.exit(1)

    TELEGRAM_TOKEN = os.getenv(key="TELEGRAM_TOKEN", default=None)
    if not TELEGRAM_TOKEN:
        # if requested model is not in the list, log an error
        print("TELEGRAM_TOKEN not set")
        sys.exit(1)

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    b = BatPuter(telegram_app=app, openai_client=client)
    b.run()
