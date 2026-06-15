import logging
import os
import sys

from dotenv import load_dotenv

import tools.gmail_search
import tools.memory
import tools.web_search
from agent import BatPuter
from connectors.telegram import TelegramConnector
from llm.mlx_client import MLXChatClient
from persistence.store import ConversationStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MODEL = "mlx-community/gemma-4-26b-a4b-it-4bit"
GMAIL_CHECK_INTERVAL = 900  # seconds
CONTEXT_WINDOW_SAFETY_MARGIN = 0.8  # leave headroom for completions and token-estimation error


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        print(f"{name} not set")
        sys.exit(1)
    return value


def _build_gmail_client():
    try:
        from connectors.gmail import GmailClient, get_gmail_service
        return GmailClient(get_gmail_service())
    except Exception as e:
        logger.warning("Gmail not available (%s) — email features disabled", e)
        return None


if __name__ == "__main__":
    load_dotenv()
    TELEGRAM_TOKEN = _require_env("TELEGRAM_TOKEN")
    TELEGRAM_CHAT_ID = int(_require_env("TELEGRAM_CHAT_ID"))

    logger.info("Loading %s ...", MODEL)
    client = MLXChatClient(MODEL)
    logger.info("Model loaded")
    tools.web_search.configure(client, MODEL)

    store = ConversationStore(os.getenv("BATPUTER_DB_PATH", "batputer.db"))
    agent = BatPuter(client, MODEL, store)
    tools.memory.configure(store, TELEGRAM_CHAT_ID)

    if client.context_length:
        agent.CONTEXT_TOKEN_BUDGET = int(client.context_length * CONTEXT_WINDOW_SAFETY_MARGIN)
        logger.info(
            "Detected context window of %d tokens, setting budget to %d",
            client.context_length, agent.CONTEXT_TOKEN_BUDGET,
        )
    else:
        logger.info("Using default context budget of %d tokens", agent.CONTEXT_TOKEN_BUDGET)

    connector = TelegramConnector(
        token=TELEGRAM_TOKEN,
        message_handler=agent.process_message,
    )

    gmail_client = _build_gmail_client()
    if gmail_client:
        tools.gmail_search.configure(gmail_client, client, MODEL)

        from tasks.gmail_monitor import GmailMonitorTask
        gmail_task = GmailMonitorTask(
            gmail_client=gmail_client,
            openai_client=client,
            model=MODEL,
            connector=connector,
            store=store,
            chat_id=TELEGRAM_CHAT_ID,
        )
        connector.app.job_queue.run_repeating(
            gmail_task.run, interval=GMAIL_CHECK_INTERVAL, first=30
        )
        logger.info("Gmail monitor scheduled every %ds", GMAIL_CHECK_INTERVAL)

    connector.run()
