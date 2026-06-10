import json
import logging
import os
import sys
import urllib.error
import urllib.request

from dotenv import load_dotenv
from openai import OpenAI

import tools.food_notes
import tools.gmail_search
import tools.web_search
from agent import BatPuter
from connectors.telegram import TelegramConnector
from persistence.store import ConversationStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

LM_STUDIO_BASE_URL = "http://localhost:1234/v1"
MODEL = "gemma-4-26b-a4b-it-mlx"
GMAIL_CHECK_INTERVAL = 900  # seconds
CONTEXT_WINDOW_SAFETY_MARGIN = 0.8  # leave headroom for completions and token-estimation error


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        print(f"{name} not set")
        sys.exit(1)
    return value


def _build_openai_client() -> OpenAI:
    client = OpenAI(base_url=LM_STUDIO_BASE_URL, api_key="lm-studio")
    available = [m.id for m in client.models.list()]
    logger.info("Available models: %s", available)
    if MODEL not in available:
        print(f"Model {MODEL!r} not found. Available: {available}")
        sys.exit(1)
    return client


def _detect_context_window(model: str) -> int | None:
    """Query LM Studio's native API for the loaded model's context window.

    Returns None if unavailable (e.g. not running against LM Studio), so
    callers can fall back to a default budget.
    """
    url = LM_STUDIO_BASE_URL.removesuffix("/v1") + "/api/v0/models"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        logger.warning("Could not query LM Studio for context window (%s)", e)
        return None
    for m in data.get("data", []):
        if m.get("id") == model:
            return m.get("loaded_context_length") or m.get("max_context_length")
    return None


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

    client = _build_openai_client()
    tools.web_search.configure(client, MODEL)

    store = ConversationStore(os.getenv("BATPUTER_DB_PATH", "batputer.db"))
    agent = BatPuter(client, MODEL, store)
    tools.food_notes.configure(store, TELEGRAM_CHAT_ID)

    context_window = _detect_context_window(MODEL)
    if context_window:
        agent.CONTEXT_TOKEN_BUDGET = int(context_window * CONTEXT_WINDOW_SAFETY_MARGIN)
        logger.info(
            "Detected context window of %d tokens, setting budget to %d",
            context_window, agent.CONTEXT_TOKEN_BUDGET,
        )
    else:
        logger.info("Using default context budget of %d tokens", agent.CONTEXT_TOKEN_BUDGET)

    connector = TelegramConnector(token=TELEGRAM_TOKEN, message_handler=agent.process_message)

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
