from persistence.store import ConversationStore


def _make_store() -> ConversationStore:
    return ConversationStore(":memory:")


def test_save_load_roundtrip():
    store = _make_store()
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ]
    for msg in messages:
        store.save_message(42, msg)

    loaded = store.load(42)
    assert len(loaded) == 3
    assert loaded[0]["role"] == "system"
    assert loaded[0]["content"] == "You are helpful."
    assert loaded[1]["role"] == "user"
    assert loaded[2]["content"] == "Hi there!"
    assert store.load(99) == []


def test_replace_all():
    store = _make_store()
    for msg in [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]:
        store.save_message(1, msg)

    compressed = [
        {"role": "system", "content": "sys"},
        {"role": "system", "content": "[Prior context]: summary"},
        {"role": "user", "content": "last"},
    ]
    store.replace_all(1, compressed)
    loaded = store.load(1)
    assert len(loaded) == 3
    assert loaded[1]["content"] == "[Prior context]: summary"


def test_seen_emails():
    store = _make_store()
    store.mark_seen(["id1", "id2", "id3"])
    unseen = store.filter_unseen(["id1", "id4", "id5"])
    assert set(unseen) == {"id4", "id5"}
    assert store.filter_unseen([]) == []


def test_food_notes_roundtrip_and_ordering():
    store = _make_store()
    assert store.get_food_notes(1) == []

    store.add_food_note(1, "note 1")
    store.add_food_note(1, "note 2")
    store.add_food_note(2, "other chat note")

    assert store.get_food_notes(1) == ["note 1", "note 2"]
    assert store.get_food_notes(2) == ["other chat note"]


def test_food_notes_limit():
    store = _make_store()
    for i in range(5):
        store.add_food_note(1, f"note {i}")

    notes = store.get_food_notes(1, limit=3)
    assert notes == ["note 2", "note 3", "note 4"]
