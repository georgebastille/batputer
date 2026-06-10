from unittest.mock import MagicMock

import tools.food_notes as food_notes


def test_remember_food_note_saves_and_acknowledges():
    store = MagicMock()
    food_notes.configure(store, 1)

    result = food_notes.remember_food_note("Garlic shrimp pasta: too salty")

    store.add_food_note.assert_called_once_with(1, "Garlic shrimp pasta: too salty")
    assert result == "Noted."
