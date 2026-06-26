"""
Database module for Jarvis AI Assistant.
Uses SQLite to persist conversations, search history, and usage statistics.
"""

import sqlite3
import datetime
import json
import os
import threading

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Data", "jarvis.db")

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

    conn.commit()
    print("[Database] Initialized successfully at:", DB_PATH)


# ─────────────────────────────────────────────────────────────────────────────
#  Conversations (replaces ChatLog.json)
# ─────────────────────────────────────────────────────────────────────────────

_current_session_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def get_session_id():
    return _current_session_id


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
        print(f"[Database] Migrated {len(data)} messages from ChatLog.json")
    except Exception as e:
        print(f"[Database] Migration skipped: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  Initialize on import
# ─────────────────────────────────────────────────────────────────────────────

initialize_database()
migrate_from_json()
