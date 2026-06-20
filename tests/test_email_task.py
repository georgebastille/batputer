import asyncio
from unittest.mock import MagicMock

from persistence.store import ConversationStore
from tasks.email_task import PerAccountEmailTask


def _gmail(emails):
    g = MagicMock()
    g.get_unread.return_value = emails
    return g


def test_new_unseen_namespaces_by_prefix_and_account():
    class T(PerAccountEmailTask):
        seen_prefix = "evt:"

    store = ConversationStore(":memory:")
    task = T([], store)
    gmail = _gmail([{"id": "1"}, {"id": "2"}])

    new = task.new_unseen("primary", gmail)
    assert [k for k, _ in new] == ["evt:primary:1", "evt:primary:2"]

    task.mark_seen([k for k, _ in new])
    assert task.new_unseen("primary", gmail) == []  # all seen now
    # different account / prefix is independent
    assert [k for k, _ in task.new_unseen("second", gmail)] == ["evt:second:1", "evt:second:2"]


def test_new_unseen_applies_predicate():
    task = PerAccountEmailTask([], ConversationStore(":memory:"))
    gmail = _gmail([{"id": "1", "from": "school@x"}, {"id": "2", "from": "spam@y"}])

    new = task.new_unseen("primary", gmail, predicate=lambda e: "school" in e["from"])
    assert [e["id"] for _, e in new] == ["1"]


def test_run_isolates_per_account_errors():
    checked = []

    class T(PerAccountEmailTask):
        async def check_account(self, label, gmail):
            checked.append(label)
            if label == "primary":
                raise RuntimeError("boom")

    task = T([("primary", MagicMock()), ("second", MagicMock())], ConversationStore(":memory:"))
    asyncio.run(task.run(None))  # must not raise

    assert checked == ["primary", "second"]  # second still ran despite first failing
