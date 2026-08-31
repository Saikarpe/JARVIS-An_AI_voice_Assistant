"""
Reminders (Phase 4.2, see ENHANCEMENT_PLAN.md).

Backend.Database's `reminders` table has existed since the original schema
refactor with nothing ever inserting into or reading it. This is the write
side — the one tool that creates a row. Backend/scheduler_worker.py's
SchedulerWorker is the read side: it polls Backend.Database.get_due_reminders()
every ~30s and speaks anything due.
"""

import datetime

import dateparser

from Backend.Database import create_reminder_row
from Backend.tools.registry import tool


@tool(
    "Set a reminder that will be spoken aloud at the given time. when_iso "
    "should be an ISO-8601 datetime (e.g. '2026-08-27T08:00:00') if you can "
    "compute one from the current date/time in your system prompt; a "
    "natural-language time like 'tomorrow at 6pm' or 'in 20 minutes' also "
    "works and will be parsed."
)
def create_reminder(message: str, when_iso: str) -> str:
    when = _parse_when(when_iso)
    if when is None:
        return f"Error: couldn't understand the time '{when_iso}'."
    if when <= datetime.datetime.now():
        return f"Error: '{when_iso}' resolved to {when.isoformat()}, which is in the past."

    create_reminder_row(message, when.isoformat(timespec="seconds"))
    return f"Reminder set for {when.strftime('%A, %d %B %Y at %H:%M')}: {message}"


def _parse_when(text: str):
    """ISO-8601 first (what the system prompt asks the model for), falling
    back to dateparser for whatever natural-language phrasing the model
    passes through verbatim instead of computing an ISO string itself."""
    try:
        return datetime.datetime.fromisoformat(text)
    except ValueError:
        pass
    return dateparser.parse(text, settings={"PREFER_DATES_FROM": "future"})
