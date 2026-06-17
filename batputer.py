import logging
import os
import sys

from dotenv import load_dotenv

import connectors.obsidian
import tools.gmail_search
import tools.memory
import tools.web_search
from agent import BatPuter
from connectors.telegram import TelegramConnector
from llm.mlx_client import MLXChatClient
from persistence.markdown_memory import MarkdownMemory
from persistence.store import ConversationStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MODEL = "mlx-community/gemma-4-26b-a4b-it-4bit"
GMAIL_CHECK_INTERVAL = 900  # seconds
MEMORY_COMPILE_INTERVAL = 1800  # seconds
DEFAULT_VAULT_PATH = "/Users/richie/Documents/BatCloudLibrary"
CONTEXT_WINDOW_SAFETY_MARGIN = 0.8  # leave headroom for completions and token-estimation error


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        print(f"{name} not set")
        sys.exit(1)
    return value


# (label, token_file, scopes). Both accounts are read-only Gmail; the primary
# account will gain the calendar scope in a later phase (needs re-consent then).
def _gmail_accounts_config():
    from connectors.gmail import GMAIL_READONLY
    return [
        ("primary", "token.json", [GMAIL_READONLY]),
        ("second", "token_second.json", [GMAIL_READONLY]),
    ]


def _build_gmail_accounts():
    """Authorise each configured account; skip any that fail. Returns [(label, GmailClient)]."""
    from connectors.gmail import GmailClient, get_gmail_service
    accounts = []
    for label, token_file, scopes in _gmail_accounts_config():
        try:
            accounts.append((label, GmailClient(get_gmail_service(token_file, scopes))))
            logger.info("Gmail account '%s' authorised", label)
        except Exception as e:
            logger.warning("Gmail account '%s' unavailable (%s) — skipping", label, e)
    return accounts


if __name__ == "__main__":
    load_dotenv()
    TELEGRAM_TOKEN = _require_env("TELEGRAM_TOKEN")
    TELEGRAM_CHAT_ID = int(_require_env("TELEGRAM_CHAT_ID"))

    logger.info("Loading %s ...", MODEL)
    client = MLXChatClient(MODEL)
    logger.info("Model loaded")
    tools.web_search.configure(client, MODEL)

    store = ConversationStore(os.getenv("BATPUTER_DB_PATH", "batputer.db"))
    vault_path = os.getenv("BATPUTER_VAULT_PATH", DEFAULT_VAULT_PATH)
    connectors.obsidian.ensure_running(os.path.basename(vault_path.rstrip("/")))
    memory = MarkdownMemory(vault_path)
    agent = BatPuter(client, MODEL, store, memory)
    tools.memory.configure(memory, TELEGRAM_CHAT_ID)

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

    gmail_accounts = _build_gmail_accounts()
    if gmail_accounts:
        tools.gmail_search.configure(gmail_accounts, client, MODEL)

        from tasks.gmail_monitor import GmailMonitorTask
        gmail_task = GmailMonitorTask(
            accounts=gmail_accounts,
            client=client,
            model=MODEL,
            connector=connector,
            store=store,
            chat_id=TELEGRAM_CHAT_ID,
        )
        connector.app.job_queue.run_repeating(
            gmail_task.run, interval=GMAIL_CHECK_INTERVAL, first=30
        )
        logger.info("Gmail monitor scheduled every %ds", GMAIL_CHECK_INTERVAL)

    from tasks.memory_compiler import MemoryCompilerTask
    compiler = MemoryCompilerTask(memory, client, MODEL, TELEGRAM_CHAT_ID)
    connector.app.job_queue.run_repeating(
        compiler.run, interval=MEMORY_COMPILE_INTERVAL, first=60
    )
    logger.info("Memory compiler scheduled every %ds", MEMORY_COMPILE_INTERVAL)

    connector.run()
