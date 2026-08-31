"""
Backend/Database.py — conversations, user_preferences, reminders, and
usage_stats read/write (Phase 6, see ENHANCEMENT_PLAN.md's test priority
list, item 3). Runs against the throwaway SQLite file tests/conftest.py
points JARVIS_DB_PATH at, never the real Data/jarvis.db.
"""

import datetime

import pytest

import Backend.Database as db


@pytest.fixture(autouse=True)
def _clean_tables():
    """Every test in this file gets a blank slate — these tables are
    small and this runs against a throwaway file anyway, so a blunt
    DELETE FROM is simpler than tracking which rows each test added."""
    conn = db._get_connection()
    for table in ("conversations", "user_preferences", "reminders", "usage_stats", "memories"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    yield


# ── conversations ──

def test_save_and_get_chat_history_round_trip():
    session = "test-session-1"
    db.save_message("user", "hello", session_id=session)
    db.save_message("assistant", "hi there", session_id=session)

    history = db.get_chat_history(session_id=session)

    assert history == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]


def test_get_chat_history_respects_limit_and_order():
    session = "test-session-2"
    for i in range(5):
        db.save_message("user", f"message {i}", session_id=session)

    history = db.get_chat_history(session_id=session, limit=3)

    # Most recent 3, but back in chronological order.
    assert [h["content"] for h in history] == ["message 2", "message 3", "message 4"]


def test_clear_chat_history_only_clears_named_session():
    db.save_message("user", "keep me", session_id="keep")
    db.save_message("user", "delete me", session_id="delete")

    db.clear_chat_history(session_id="delete")

    assert db.get_chat_history(session_id="delete") == []
    assert len(db.get_chat_history(session_id="keep")) == 1


def test_get_sessions_groups_and_previews():
    db.save_message("user", "first session first message", session_id="s1")
    db.save_message("assistant", "reply", session_id="s1")
    db.save_message("user", "second session message", session_id="s2")

    sessions = {s["session_id"]: s for s in db.get_sessions()}

    assert sessions["s1"]["message_count"] == 2
    assert sessions["s1"]["preview"] == "first session first message"
    assert sessions["s2"]["message_count"] == 1


def test_set_session_id_changes_the_default_target():
    db.set_session_id("switched-to")
    db.save_message("user", "no explicit session_id")  # should land in "switched-to"

    assert db.get_chat_history(session_id="switched-to") == [
        {"role": "user", "content": "no explicit session_id"}
    ]
    # Restore, so this doesn't bleed into later tests in the same session.
    db.set_session_id(db.get_session_id())


# ── user_preferences ──

def test_preference_round_trip_and_default():
    assert db.get_preference("nonexistent_key") is None
    assert db.get_preference("nonexistent_key", default="fallback") == "fallback"

    db.set_preference("theme", "dark")
    assert db.get_preference("theme") == "dark"

    db.set_preference("theme", "light")  # INSERT OR REPLACE
    assert db.get_preference("theme") == "light"


# ── reminders ──

def test_reminder_lifecycle():
    future = (datetime.datetime.now() + datetime.timedelta(minutes=5)).isoformat()
    past = (datetime.datetime.now() - datetime.timedelta(minutes=5)).isoformat()

    db.create_reminder_row("future reminder", future)
    db.create_reminder_row("overdue reminder", past)

    due = db.get_due_reminders()
    due_messages = {row["message"] for row in due}

    assert "overdue reminder" in due_messages
    assert "future reminder" not in due_messages

    for row in due:
        db.mark_reminder_done(row["id"])

    assert db.get_due_reminders() == []


# ── usage_stats ──

def test_usage_summary_aggregates_correctly():
    db.log_usage("q1", "general", 100, status="success")
    db.log_usage("q2", "web_search", 200, status="success")
    db.log_usage("q3", "web_search", 300, status="error", error_message="boom")

    summary = db.get_usage_summary()

    assert summary["total_queries"] == 3
    assert summary["successful"] == 2
    assert summary["failed"] == 1
    assert summary["avg_response_time_ms"] == 150.0  # average of the two *successful* calls
    assert summary["queries_by_type"] == {"web_search": 2, "general": 1}
