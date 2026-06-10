from tools.commons import tool

_STORE = None
_CHAT_ID = None


def configure(store, chat_id) -> None:
    global _STORE, _CHAT_ID
    _STORE = store
    _CHAT_ID = chat_id


@tool
def remember_food_note(note: str) -> str:
    """Save a note about the user's food/recipe preferences or feedback for future reference.

    Args:
        note: A concise note capturing what the user liked, disliked, or
            learned about a dish or recipe (e.g. "Garlic shrimp pasta: too
            salty, use less soy sauce next time; loved the lemon zest").
    """
    _STORE.add_food_note(_CHAT_ID, note)
    return "Noted."
