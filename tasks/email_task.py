"""Shared machinery for scheduled tasks that scan Gmail.

Each email task (action triage, school events, invoices) walks the same path:
loop the configured accounts with per-account error isolation, find the unread
mail it hasn't processed yet, and mark mail processed so it isn't handled twice.
That mechanics lives here so subclasses only express their own logic in
`check_account`; `seen_prefix` keeps each task's dedup independent.
"""
import logging

logger = logging.getLogger(__name__)


class PerAccountEmailTask:
    seen_prefix = ""  # override per task, e.g. "evt:" or "inv:"

    def __init__(self, accounts, store):
        self._accounts = accounts
        self._store = store

    async def run(self, context) -> None:
        for label, gmail in self._accounts:
            try:
                await self.check_account(label, gmail)
            except Exception:
                logger.exception("%s failed for account %s", type(self).__name__, label)

    def new_unseen(self, label, gmail, predicate=None) -> "list[tuple[str, dict]]":
        """(seen_key, email) pairs for unread mail this task hasn't processed yet,
        optionally restricted to emails matching `predicate`. Keys are namespaced
        by task and account so message ids can't collide."""
        emails = gmail.get_unread()
        if predicate is not None:
            emails = [e for e in emails if predicate(e)]
        keyed = {f"{self.seen_prefix}{label}:{e['id']}": e for e in emails}
        return [(key, keyed[key]) for key in self._store.filter_unseen(list(keyed))]

    def mark_seen(self, keys) -> None:
        self._store.mark_seen(list(keys))

    async def check_account(self, label: str, gmail) -> None:
        raise NotImplementedError
