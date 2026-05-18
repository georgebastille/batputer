import os
from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from telegram import Update
from openai import OpenAI

client = OpenAI(base_url="http://192.168.50.132:1234/v1", api_key="lm-studio")
MODEL = "gemma-4-26b-a4b-it-mlx"


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    system_prompt = {
        "role": "system",
        "content": "You are a helpful assistant. You ill respond with a witty joke based on what the user send to you.",
    }
    user_message = {"role": "user", "content": update.message.text}

    conversation = []
    conversation.append(system_prompt)
    conversation.append(user_message)
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action="typing"
    )
    response = client.chat.completions.create(
        model=MODEL,
        messages=conversation,
        extra_body={"thinking": {"type": "disabled"}},
    )
    reply = response.choices[0].message.content

    await context.bot.send_message(chat_id=update.effective_chat.id, text=reply)


if __name__ == "__main__":
    models = client.models.list()
    # if requested model is not in the list, log an error
    load_dotenv()
    TELEGRAM_TOKEN = os.getenv(key="TELEGRAM_TOKEN")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT, handle))
    app.run_polling()
