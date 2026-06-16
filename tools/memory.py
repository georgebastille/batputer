from tools.commons import tool

_MEMORY = None
_CHAT_ID = None


def configure(memory, chat_id) -> None:
    global _MEMORY, _CHAT_ID
    _MEMORY = memory
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
    _MEMORY.append_raw(content, profile=profile)
    return "Remembered."


@tool
def recall_memory(query: str) -> str:
    """Search the user's personal Obsidian vault/notes and saved memories for facts.

    Use this for any question about the user's life, family, home, schedule, or plans
    before saying you don't know — the answer is often already in their notes.

    Args:
        query: Keywords describing what to look for (e.g. "Willow uniform size",
            "Italy trip", "daughter school"). Use distinctive nouns and names.
    """
    results = _MEMORY.search(query)
    if not results:
        return "No matching memories found."
    return "\n".join(f"- {r}" for r in results)
