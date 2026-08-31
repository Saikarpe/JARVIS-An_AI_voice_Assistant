"""
Long-term memory tools (Phase 4.1, see ENHANCEMENT_PLAN.md).

Before this, everything the agent "knew" about the user was the last N
messages of the *current* session (Backend.Database.get_chat_history) —
nothing survived a restart, and nothing was retrieved by relevance. These
two tools let the model manage its own memory explicitly: save a fact worth
keeping, and search what it already knows before assuming it doesn't know
something. Backend/agent.py also calls Backend.Database.recall() directly,
unprompted, on every turn — recall_memory exists for when the model wants
a *second*, differently-worded search mid-conversation rather than relying
only on that first automatic pass.
"""

from Backend.Database import recall, remember
from Backend.tools.registry import tool


@tool(
    "Save a durable fact, preference, or event about the user for future "
    "conversations — something worth remembering beyond this session, like "
    "their favorite team, a project they're working on, or an upcoming "
    "date. Don't use this for one-off information that's only relevant to "
    "the current question."
)
def remember_fact(fact: str) -> str:
    try:
        remember(fact, kind="fact")
    except Exception as e:
        return f"Error saving memory: {e}"
    return f"Remembered: {fact}"


@tool(
    "Search your long-term memory about the user — things saved with "
    "remember_fact in this or earlier conversations. Use this when the "
    "user references something you might already know (\"like last time\", "
    "\"you know my preference for...\") that isn't in the recent chat history."
)
def recall_memory(query: str) -> str:
    try:
        results = recall(query)
    except Exception as e:
        return f"Error searching memory: {e}"
    if not results:
        return "No relevant memories found."
    return "\n".join(f"- {r}" for r in results)
