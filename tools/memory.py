from tools.commons import tool

_STORE = None
_CHAT_ID = None


def configure(store, chat_id) -> None:
    global _STORE, _CHAT_ID
    _STORE = store
    _CHAT_ID = chat_id


@tool
def remember(content: str, profile: bool = False) -> str:
    """Save a fact to remember about the user or their family for future conversations.

    Args:
        content: The fact to remember, written so it stands alone (e.g.
            "Daughter's name is Mia, age 8" or "User is planning a trip to
            Italy in September").
        profile: True for core, stable facts that should always be available
            (names, family members, key long-term preferences). False (default)
            for situational notes that only matter sometimes.
    """
    _STORE.add_memory(_CHAT_ID, content, "profile" if profile else "general")
    return "Remembered."


@tool
def recall_memory(query: str) -> str:
    """Search saved memories for facts relevant to a topic.

    Args:
        query: Keywords describing what to look for (e.g. "Italy trip", "daughter school").
    """
    results = _STORE.search_memories(_CHAT_ID, query)
    if not results:
        return "No matching memories found."
    return "\n".join(f"- {r}" for r in results)
