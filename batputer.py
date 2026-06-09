import logging
import os
import sys

from dotenv import load_dotenv
from openai import OpenAI

import tools.web_search
from agent import BatPuter
from connectors.telegram import TelegramConnector
from persistence.store import ConversationStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MODEL = "gemma-4-26b-a4b-it-mlx"
GMAIL_CHECK_INTERVAL = 900  # seconds


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        print(f"{name} not set")
        sys.exit(1)
    return value


def _build_openai_client() -> OpenAI:
    client = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")
    available = [m.id for m in client.models.list()]
    logger.info("Available models: %s", available)
    if MODEL not in available:
        print(f"Model {MODEL!r} not found. Available: {available}")
        sys.exit(1)
    return client


def _build_gmail_task(client, connector, store, chat_id):
    try:
        from tasks.gmail_monitor import GmailMonitorTask, get_gmail_service
        gmail_service = get_gmail_service()
        return GmailMonitorTask(
            gmail_service=gmail_service,
            openai_client=client,
            model=MODEL,
            connector=connector,
            store=store,
            chat_id=chat_id,
        )
    except Exception as e:
        logger.warning("Gmail not available (%s) — email monitoring disabled", e)
        return None


if __name__ == "__main__":
    load_dotenv()
    TELEGRAM_TOKEN = _require_env("TELEGRAM_TOKEN")
    TELEGRAM_CHAT_ID = int(_require_env("TELEGRAM_CHAT_ID"))

    client = _build_openai_client()
    tools.web_search.configure(client, MODEL)

    store = ConversationStore(os.getenv("BATPUTER_DB_PATH", "batputer.db"))
    agent = BatPuter(client, MODEL, store)
    connector = TelegramConnector(token=TELEGRAM_TOKEN, message_handler=agent.process_message)

    gmail_task = _build_gmail_task(client, connector, store, TELEGRAM_CHAT_ID)
    if gmail_task:
        connector.app.job_queue.run_repeating(
            gmail_task.run, interval=GMAIL_CHECK_INTERVAL, first=30
        )
        logger.info("Gmail monitor scheduled every %ds", GMAIL_CHECK_INTERVAL)

    connector.run()
