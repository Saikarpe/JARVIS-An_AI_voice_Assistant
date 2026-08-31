"""
Database module for Jarvis AI Assistant.
Uses SQLite to persist conversations, search history, and usage statistics.
"""

import datetime
import json
import logging
import os
import sqlite3
import threading

logger = logging.getLogger(__name__)

# JARVIS_DB_PATH override (Phase 6): lets tests/conftest.py point every
# module-level connection at a throwaway file instead of the real
# Data/jarvis.db — set before this module is first imported anywhere in
# the process, since DB_PATH is read once here and initialize_database()
# runs at import time (see bottom of this file). Unset in normal use; the
# default is unchanged.
DB_PATH = os.environ.get("JARVIS_DB_PATH") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Data", "jarvis.db"
)

# Thread-local storage for connections (SQLite connections are not thread-safe)
_local = threading.local()


def _get_connection():
    """Get a thread-local database connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")  # Better concurrency
    return _local.conn


def initialize_database():
    """Create all tables if they don't exist."""
    conn = _get_connection()
    cursor = conn.cursor()

    # ── Conversations table (replaces ChatLog.json) ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
            content TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Search history table ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS search_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            search_results TEXT,
            ai_response TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Usage statistics table ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usage_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            query_type TEXT NOT NULL,
            response_time_ms INTEGER,
            status TEXT DEFAULT 'success',
            error_message TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── User preferences table ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_preferences (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Reminders table ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT NOT NULL,
            remind_at DATETIME NOT NULL,
            is_completed INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Long-term memory table (Phase 4.1, see ENHANCEMENT_PLAN.md) ──
    # embedding is a float32 numpy array's raw .tobytes() — fixed-width
    # (384 dims * 4 bytes for BAAI/bge-small-en-v1.5), so no length column
    # is needed to unpack it back with np.frombuffer(..., dtype='float32').
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'fact',
            embedding BLOB NOT NULL,
            importance REAL DEFAULT 0.5,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_used DATETIME
        )
    """)

    conn.commit()
    logger.info("Initialized successfully at: %s", DB_PATH)


# ─────────────────────────────────────────────────────────────────────────────
#  Conversations (replaces ChatLog.json)
# ─────────────────────────────────────────────────────────────────────────────

_current_session_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def get_session_id():
    return _current_session_id


def set_session_id(session_id: str):
    """Switch the *active* session — Phase 5.5's conversation history
    sidebar calls this when the user picks a past session to resume, so
    the next save_message()/get_chat_history() (no explicit session_id
    passed) targets that session instead of always the one created at
    process start."""
    global _current_session_id
    _current_session_id = session_id


def get_sessions(limit=50):
    """One row per session: id, first message's preview text, message
    count, and when it was last active — most recent first. Backs the
    Phase 5.5 history sidebar; get_all_chat_history() already existed but
    returns one flat list across every session with no way to group it."""
    conn = _get_connection()
    rows = conn.execute(
        """
        SELECT session_id,
               COUNT(*) AS message_count,
               MIN(timestamp) AS started_at,
               MAX(timestamp) AS last_active
        FROM conversations
        GROUP BY session_id
        ORDER BY last_active DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    sessions = []
    for row in rows:
        first = conn.execute(
            "SELECT content FROM conversations WHERE session_id = ? AND role = 'user' "
            "ORDER BY id ASC LIMIT 1",
            (row["session_id"],),
        ).fetchone()
        preview = (first["content"] if first else "").strip()
        if len(preview) > 60:
            preview = preview[:60] + "..."
        sessions.append({
            "session_id": row["session_id"],
            "preview": preview or "(empty)",
            "message_count": row["message_count"],
            "started_at": row["started_at"],
            "last_active": row["last_active"],
        })
    return sessions


def save_message(role, content, session_id=None):
    """Save a conversation message to the database."""
    conn = _get_connection()
    sid = session_id or _current_session_id
    conn.execute(
        "INSERT INTO conversations (session_id, role, content) VALUES (?, ?, ?)",
        (sid, role, content)
    )
    conn.commit()


def get_chat_history(session_id=None, limit=50):
    """Retrieve recent chat history as a list of dicts (compatible with Groq API format)."""
    conn = _get_connection()
    sid = session_id or _current_session_id
    cursor = conn.execute(
        "SELECT role, content FROM conversations WHERE session_id = ? ORDER BY id DESC LIMIT ?",
        (sid, limit)
    )
    rows = cursor.fetchall()
    # Reverse to get chronological order
    return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]


def get_all_chat_history(limit=200):
    """Get all chat history across sessions (for GUI display)."""
    conn = _get_connection()
    cursor = conn.execute(
        "SELECT role, content, timestamp, session_id FROM conversations ORDER BY id DESC LIMIT ?",
        (limit,)
    )
    return [dict(row) for row in reversed(cursor.fetchall())]


def clear_chat_history(session_id=None):
    """Clear chat history for a session (or current session)."""
    conn = _get_connection()
    sid = session_id or _current_session_id
    conn.execute("DELETE FROM conversations WHERE session_id = ?", (sid,))
    conn.commit()


def export_chat_as_json():
    """Export current session chat as JSON (backwards compatible with ChatLog.json)."""
    history = get_chat_history()
    return json.dumps(history, indent=4)


# ─────────────────────────────────────────────────────────────────────────────
#  Search History
# ─────────────────────────────────────────────────────────────────────────────

def save_search(query, search_results="", ai_response=""):
    """Log a web search query and its results."""
    conn = _get_connection()
    conn.execute(
        "INSERT INTO search_history (query, search_results, ai_response) VALUES (?, ?, ?)",
        (query, search_results, ai_response)
    )
    conn.commit()


def get_recent_searches(limit=10):
    """Get recent search queries."""
    conn = _get_connection()
    cursor = conn.execute(
        "SELECT query, ai_response, timestamp FROM search_history ORDER BY id DESC LIMIT ?",
        (limit,)
    )
    return [dict(row) for row in cursor.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
#  Usage Statistics
# ─────────────────────────────────────────────────────────────────────────────

def log_usage(query, query_type, response_time_ms, status="success", error_message=None):
    """Log a usage event (every query processed by Jarvis)."""
    conn = _get_connection()
    conn.execute(
        "INSERT INTO usage_stats (query, query_type, response_time_ms, status, error_message) VALUES (?, ?, ?, ?, ?)",
        (query, query_type, response_time_ms, status, error_message)
    )
    conn.commit()


def get_usage_summary():
    """Get a summary of usage statistics — perfect for presentations!"""
    conn = _get_connection()

    total = conn.execute("SELECT COUNT(*) as cnt FROM usage_stats").fetchone()["cnt"]
    successful = conn.execute("SELECT COUNT(*) as cnt FROM usage_stats WHERE status = 'success'").fetchone()["cnt"]
    failed = conn.execute("SELECT COUNT(*) as cnt FROM usage_stats WHERE status = 'error'").fetchone()["cnt"]
    avg_time = conn.execute("SELECT AVG(response_time_ms) as avg_ms FROM usage_stats WHERE status = 'success'").fetchone()["avg_ms"]

    type_counts = conn.execute(
        "SELECT query_type, COUNT(*) as cnt FROM usage_stats GROUP BY query_type ORDER BY cnt DESC"
    ).fetchall()

    return {
        "total_queries": total,
        "successful": successful,
        "failed": failed,
        "avg_response_time_ms": round(avg_time, 1) if avg_time else 0,
        "queries_by_type": {row["query_type"]: row["cnt"] for row in type_counts},
    }


# ─────────────────────────────────────────────────────────────────────────────
#  User Preferences
# ─────────────────────────────────────────────────────────────────────────────

def set_preference(key, value):
    """Save or update a user preference."""
    conn = _get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO user_preferences (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
        (key, value)
    )
    conn.commit()


def get_preference(key, default=None):
    """Get a user preference."""
    conn = _get_connection()
    row = conn.execute("SELECT value FROM user_preferences WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


# ─────────────────────────────────────────────────────────────────────────────
#  Reminders (Phase 4.2, see ENHANCEMENT_PLAN.md) — the table has existed
#  since the original schema; nothing ever read or wrote it until now.
#  Backend/tools/reminders.py is the write side (the create_reminder tool),
#  Backend/scheduler_worker.py's SchedulerWorker is the read side (polls
#  get_due_reminders() every ~30s and speaks anything that's due).
# ─────────────────────────────────────────────────────────────────────────────

def create_reminder_row(message, remind_at_iso):
    """remind_at_iso must already be a valid ISO-8601 string — parsing/
    natural-language handling (dateparser) lives in the tool layer, not
    here, so this module stays a thin, dumb persistence layer."""
    conn = _get_connection()
    conn.execute(
        "INSERT INTO reminders (message, remind_at) VALUES (?, ?)",
        (message, remind_at_iso),
    )
    conn.commit()


def get_due_reminders(now_iso=None):
    """Reminders that are due and haven't fired yet, oldest due-time first."""
    conn = _get_connection()
    now_iso = now_iso or datetime.datetime.now().isoformat(timespec="seconds")
    cursor = conn.execute(
        "SELECT id, message, remind_at FROM reminders "
        "WHERE is_completed = 0 AND remind_at <= ? ORDER BY remind_at",
        (now_iso,),
    )
    return [dict(row) for row in cursor.fetchall()]


def mark_reminder_done(reminder_id):
    conn = _get_connection()
    conn.execute("UPDATE reminders SET is_completed = 1 WHERE id = ?", (reminder_id,))
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
#  Long-term memory (Phase 4.1, see ENHANCEMENT_PLAN.md)
# ─────────────────────────────────────────────────────────────────────────────
#  fastembed is imported lazily, inside _get_embedder(), not at module load:
#  Database.py is imported very early by almost everything (agent.py,
#  agent_worker.py, config.py, every tool), and fastembed's first call
#  downloads a ~130MB ONNX model — that shouldn't become a startup cost (or
#  a hard crash on a machine with no internet) for code paths that never
#  touch memory at all. remember()/recall() below fail loudly if the import
#  or download fails; Backend/tools/memory.py's tool wrappers are what
#  catch that and turn it into a normal "Error: ..." tool result instead of
#  crashing the agent loop.

_embedder = None
_EMBED_MODEL = "BAAI/bge-small-en-v1.5"  # 384-dim, ONNX, CPU-only, ~130MB


def _get_embedder():
    global _embedder
    if _embedder is None:
        from fastembed import TextEmbedding
        _embedder = TextEmbedding(_EMBED_MODEL)
    return _embedder


def remember(content, kind="fact", importance=0.5):
    """Save a durable fact/preference/event, embedded for later semantic
    recall(). Not deduplicated — deciding whether a new fact supersedes an
    old one is a judgment call left to the model (via the recall_memory
    tool showing it what's already stored), not something this layer
    tries to guess with a similarity threshold."""

    vec = next(_get_embedder().embed([content])).astype("float32")
    conn = _get_connection()
    conn.execute(
        "INSERT INTO memories (content, kind, embedding, importance) VALUES (?, ?, ?, ?)",
        (content, kind, vec.tobytes(), importance),
    )
    conn.commit()


def recall(query, k=5, min_score=0.35):
    """Brute-force cosine similarity over every stored memory. The plan's
    own sizing note applies: this is well under a millisecond for a few
    thousand rows, so there's no vector DB here — add sqlite-vec only if
    this table ever exceeds ~50k rows."""
    import numpy as np

    conn = _get_connection()
    rows = conn.execute("SELECT id, content, embedding FROM memories").fetchall()
    if not rows:
        return []

    qv = next(_get_embedder().embed([query])).astype("float32")
    qnorm = np.linalg.norm(qv)
    if qnorm == 0:
        return []

    scored = []
    for row in rows:
        v = np.frombuffer(row["embedding"], dtype="float32")
        vnorm = np.linalg.norm(v)
        if vnorm == 0:
            continue
        score = float(qv @ v / (qnorm * vnorm))
        scored.append((score, row["id"], row["content"]))
    scored.sort(key=lambda s: s[0], reverse=True)
    top = [s for s in scored[:k] if s[0] > min_score]

    if top:
        ids = tuple(rid for _, rid, _ in top)
        placeholders = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE memories SET last_used = CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
            ids,
        )
        conn.commit()

    return [content for _, _, content in top]


def clear_memories():
    """Backs the Phase 5.5 settings panel's "clear memory" button."""
    conn = _get_connection()
    conn.execute("DELETE FROM memories")
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
#  Migrate existing ChatLog.json into the database
# ─────────────────────────────────────────────────────────────────────────────

def migrate_from_json():
    """One-time migration: import existing ChatLog.json into the database."""
    json_path = os.path.join(os.path.dirname(DB_PATH), "ChatLog.json")
    if not os.path.exists(json_path):
        return

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not data:
            return

        conn = _get_connection()
        # Check if we already migrated
        existing = conn.execute("SELECT COUNT(*) as cnt FROM conversations WHERE session_id = 'migrated'").fetchone()["cnt"]
        if existing > 0:
            return

        for entry in data:
            conn.execute(
                "INSERT INTO conversations (session_id, role, content) VALUES (?, ?, ?)",
                ("migrated", entry.get("role", "user"), entry.get("content", ""))
            )
        conn.commit()
        logger.info("Migrated %d messages from ChatLog.json", len(data))
    except Exception as e:
        logger.warning("Migration skipped: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
#  Initialize on import
# ─────────────────────────────────────────────────────────────────────────────

initialize_database()
migrate_from_json()
