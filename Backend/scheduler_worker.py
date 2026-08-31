"""
SchedulerWorker (Phase 4.2 / 4.3, see ENHANCEMENT_PLAN.md).

Runs on its own QThread — wired up in main.py exactly like AgentWorker
(Phase 1) — wakes roughly every POLL_SECONDS, and:

  - fires any reminder whose remind_at has passed (Phase 4.2). Backend.
    Database's `reminders` table has existed since the original schema
    refactor with nothing ever populating or reading it;
    Backend/tools/reminders.py's create_reminder tool is the write side,
    this is the read side.
  - optionally runs a once-a-day "morning briefing" agent turn at a
    configured time (Phase 4.3), gated behind Backend.config.settings'
    proactive_enabled + morning_briefing_enabled — both default off, per
    the plan's explicit "keep it small and predictable" instruction for
    proactive behavior; an assistant that talks unprompted at the wrong
    moment is worse than one that stays quiet.

Speaks reminders/briefings directly via Backend.TextToSpeech rather than
routing through AgentWorker's query pipeline — a reminder doesn't need an
LLM turn, it needs its stored text spoken, so there's no agent loop to
hand it to. AgentWorker and SchedulerWorker can therefore both call
TextToSpeech from different threads; TTS()'s only shared mutable state is
the module-level _STOP_EVENT (Backend/TextToSpeech.py), so in the worst
case a reminder firing mid-answer could interrupt or be interrupted by it
— an acceptable edge case for a background reminder, not one worth a
cross-thread lock over.

The retry/backoff discipline this codebase uses everywhere else
(ChatBot's bounded retry, every tool's try/except) applies here too: run()
never lets an exception from one poll kill the loop, since a dead
scheduler thread means reminders silently stop firing with no crash to
notice.
"""

import datetime
import logging
import time

from PyQt5.QtCore import QObject, pyqtSignal

from Backend.config import settings
from Backend.Database import get_due_reminders, mark_reminder_done
from Backend.TextToSpeech import TextToSpeech

logger = logging.getLogger(__name__)

POLL_SECONDS = 30


class SchedulerWorker(QObject):
    reminder_fired = pyqtSignal(str)   # reminder message, for a GUI toast (Phase 5.5)
    briefing_fired = pyqtSignal(str)   # briefing text, for the chat log

    def __init__(self):
        super().__init__()
        self._running = True
        self._last_briefing_date = None  # date() the briefing last ran, so it fires once/day

    def shutdown(self):
        self._running = False

    def run(self):
        while self._running:
            try:
                self._check_reminders()
                self._check_briefing()
            except Exception as e:
                logger.exception("error in scheduler poll: %s", e)
            # Sleep in 1s ticks rather than one time.sleep(POLL_SECONDS) so
            # shutdown() takes effect within a second instead of waiting
            # out the full poll interval — the same reasoning as
            # WakeWordDetector's per-frame should_continue() check.
            for _ in range(POLL_SECONDS):
                if not self._running:
                    break
                time.sleep(1)

    def _check_reminders(self):
        for row in get_due_reminders():
            self.reminder_fired.emit(row["message"])
            try:
                TextToSpeech(f"Reminder: {row['message']}")
            except Exception as e:
                logger.warning("failed to speak reminder %s: %s", row["id"], e)
            # Marked done even if speaking failed — a reminder that can't
            # be spoken shouldn't re-fire forever on every future poll.
            mark_reminder_done(row["id"])

    def _check_briefing(self):
        if not (settings.proactive_enabled and settings.morning_briefing_enabled):
            return
        now = datetime.datetime.now()
        if now.strftime("%H:%M") != settings.morning_briefing_time:
            return
        if self._last_briefing_date == now.date():
            return  # already ran today
        self._last_briefing_date = now.date()

        # Imported here, not at module level: this is the one code path in
        # the whole app that pulls in the full agent loop (Groq client,
        # every tool) from a background thread that, with proactive
        # behaviors off (the default), never needs any of it.
        from Backend.agent import run_agent

        try:
            text = run_agent(
                "Give me a short morning briefing: today's date, one "
                "interesting fact or headline, and a friendly greeting. "
                "Keep it to 2-3 sentences — this will be spoken aloud."
            )
        except Exception as e:
            logger.warning("briefing generation failed: %s", e)
            return

        self.briefing_fired.emit(text)
        try:
            TextToSpeech(text)
        except Exception as e:
            logger.warning("failed to speak briefing: %s", e)
