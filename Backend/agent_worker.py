"""
AgentWorker — runs the voice/agent pipeline off the GUI thread and talks to it
exclusively via Qt signals (see ENHANCEMENT_PLAN.md, Phase 1).

This replaces the old design where main.py's FirstThread wrote state to
Frontend/Files/*.data and three separate QTimers in Frontend/GUI.py polled
those files to find out what happened. That added up to a second of latency,
had no way to stream a response, and could race (a fast second response
could get overwritten by a stale first one before the poll caught up).

As of Phase 2, query handling is a single call into Backend.agent.run_agent
— a real tool-calling agent loop — instead of a fixed classify-then-route
pipeline (Cohere decision string -> Automation/ChatBot/RealtimeSearchEngine
picked by string prefix). "stop" and "goodbye" stay as small local checks
here rather than going through the model: they're control actions that need
to be instant and 100% reliable, not something worth a network round trip.

As of Phase 3, listening is gated on a wake word (Backend/wake_word.py)
instead of the mic being hot the instant it's enabled, and speech playback
can be interrupted by talking over it (Backend/barge_in.py).

AgentWorker owns no widgets. It is moved to a QThread in main.py and driven
entirely by:
  - slots (GUI -> worker): handle_text_query, set_mic_enabled, stop_speaking,
    handle_file_uploaded, shutdown
  - signals (worker -> GUI): state_changed, user_message, token,
    response_done, tool_started, tool_finished, error. audio_level carries
    a real 0.0-1.0 RMS level from the mic during listening and from the
    decoded TTS output during speaking (Phase 5.4, see
    Backend/SpeechToText.py and Backend/TextToSpeech.py). partial_transcript
    is wired up but not yet emitted — it would need streaming ASR, which
    Whisper's non-streaming API doesn't provide.
"""

import logging
import os
import queue
import re
import time

from dotenv import dotenv_values
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from Backend.agent import run_agent
from Backend.barge_in import BargeInMonitor
from Backend.config import BARGE_IN_ENABLED, WAKE_WORD_ENABLED, settings
from Backend.SpeechToText import SpeechRecognition
from Backend.TextToSpeech import TextToSpeech, request_stop
from Backend.wake_word import WakeWordDetector

logger = logging.getLogger(__name__)

env_vars = dotenv_values(".env")
Username = env_vars.get("Username") or "User"
Assistantname = env_vars.get("Assistantname") or "Jarvis"

# Short, exact(-ish) control phrases handled locally instead of by the
# model — these need to be instant, and "stop" in the middle of a longer
# sentence ("how do I stop overthinking") should NOT trigger them, so we
# only match short queries against a curated set.
_STOP_PHRASES = {"stop", "stop it", "be quiet", "shut up", "quiet", "pause"}
_EXIT_PHRASES = {"bye", "bye jarvis", "goodbye", "good bye", "exit", "quit", "see you"}


def _normalize(text: str) -> str:
    return re.sub(r"[.?!]+$", "", text.strip().lower())


class AgentWorker(QObject):
    """Runs the full voice/agent pipeline off the GUI thread.
    Owns no widgets. Communicates only via signals."""

    state_changed = pyqtSignal(str)          # 'listening'|'thinking'|'tool'|'speaking'|'available'
    partial_transcript = pyqtSignal(str)      # reserved for Phase 3 live STT
    user_message = pyqtSignal(str)            # finalized user turn
    token = pyqtSignal(str)                   # one streamed token of the reply
    response_done = pyqtSignal(str)           # full assistant reply text
    tool_started = pyqtSignal(str, dict)      # tool name, arguments
    tool_finished = pyqtSignal(str, str)      # tool name, short result summary
    audio_level = pyqtSignal(float)           # reserved for Phase 3/5.4 visualizer
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._mic_enabled = True
        self._running = True
        self._text_queue: "queue.Queue[str]" = queue.Queue()
        self._uploaded_file = None
        # Loading the wake-word model does file I/O and a possible network
        # fetch on first run; doing it once here (not per-tick) keeps the
        # main loop's steady-state cheap. WakeWordDetector fails soft --
        # .available is False if the model couldn't be loaded, and _tick
        # falls back to always-on listening in that case.
        self._wake_detector = (
            WakeWordDetector(threshold=settings.wake_word_threshold) if WAKE_WORD_ENABLED else None
        )

    # ────────────────────────────────────────────────────────────────
    #  Slots — GUI thread calls these. Connected with Qt.DirectConnection
    #  in Frontend.GUI.MainWindow.attach_worker() (see that file for why).
    # ────────────────────────────────────────────────────────────────
    @pyqtSlot(str)
    def handle_text_query(self, text: str):
        if text and text.strip():
            self._text_queue.put(text.strip())

    @pyqtSlot(bool)
    def set_mic_enabled(self, enabled: bool):
        self._mic_enabled = enabled

    @pyqtSlot(str)
    def handle_file_uploaded(self, path: str):
        self._uploaded_file = path

    @pyqtSlot()
    def stop_speaking(self):
        request_stop()

    @pyqtSlot()
    def shutdown(self):
        self._running = False

    # ────────────────────────────────────────────────────────────────
    #  Entry point — connected to QThread.started in main.py
    # ────────────────────────────────────────────────────────────────
    def run(self):
        self._emit_initial_history()
        while self._running:
            try:
                self._tick()
            except Exception as e:
                logger.exception("unexpected error in the main loop")
                self.error.emit(str(e))
                time.sleep(1)

    def _emit_initial_history(self):
        """Replay recent chat history (or a welcome message) so the chat
        pane isn't empty on launch. Reads straight from SQLite — no more
        Database.data / ChatLog.json round-trip."""
        try:
            from Backend.Database import get_chat_history
            history = get_chat_history(limit=20)
        except Exception:
            history = []

        if not history:
            self.user_message.emit(f"Hello {Assistantname}, how are you?")
            self.response_done.emit(
                f"Welcome {Username}. I am doing well. How may I help you?"
            )
            return

        for entry in history:
            if entry["role"] == "user":
                self.user_message.emit(entry["content"])
            elif entry["role"] == "assistant":
                self.response_done.emit(entry["content"])

    def _tick(self):
        """One iteration of the main loop: prefer a typed query if one is
        waiting, otherwise listen on the mic (if enabled)."""
        try:
            text_query = self._text_queue.get_nowait()
        except queue.Empty:
            text_query = None

        if text_query:
            self._process_query(text_query)
            return

        if not self._mic_enabled:
            self.state_changed.emit("available")
            time.sleep(0.05)
            return

        if self._wake_detector is not None and self._wake_detector.available:
            self.state_changed.emit("available")
            # Polled every ~80ms (one audio frame), so toggling the mic off
            # or shutting down while waiting for the wake word reacts almost
            # immediately — unlike the old design's up-to-10-second blocking
            # listen() with no way to interrupt it early.
            heard_wake_word = self._wake_detector.listen(
                should_continue=lambda: self._running and self._mic_enabled
            )
            if not heard_wake_word:
                return  # mic got disabled or shutdown requested mid-wait

        self.state_changed.emit("listening")
        # NOTE: with the wake word active, this still blocks for the actual
        # command (up to MAX_UTTERANCE_SECONDS) once "hey jarvis" is heard —
        # that's expected, the user is mid-sentence. Without a working wake
        # detector this is the only gate before listening, same as
        # pre-Phase-3 behavior. A typed query submitted during this window
        # still won't be picked up until it returns.
        query = SpeechRecognition(status_cb=self.state_changed.emit, level_cb=self.audio_level.emit)
        if not query or not query.strip():
            self.state_changed.emit("available")
            return
        self._process_query(query)

    # ────────────────────────────────────────────────────────────────
    #  Query processing
    # ────────────────────────────────────────────────────────────────
    def _process_query(self, query: str):
        self.user_message.emit(query)
        normalized = _normalize(query)

        if normalized in _STOP_PHRASES:
            request_stop()
            self.state_changed.emit("available")
            self.response_done.emit("Okay, stopping.")
            return

        if normalized in _EXIT_PHRASES:
            request_stop()
            self.response_done.emit("Okay, Bye!")
            self._speak("Okay, Bye!")
            self.shutdown()
            os._exit(0)  # explicit, user-requested full exit — not a lint suppression

        if self._uploaded_file:
            from Backend.file_context import build_attachment_context
            query = query + build_attachment_context(self._uploaded_file)
            self._uploaded_file = None

        answer = run_agent(
            query,
            on_state=self.state_changed.emit,
            on_tool_start=self.tool_started.emit,
            on_tool_end=self.tool_finished.emit,
            on_token=self.token.emit,
        )
        self.response_done.emit(answer)
        self._speak(answer)

    def _speak(self, answer: str):
        self.state_changed.emit("speaking")
        if BARGE_IN_ENABLED:
            monitor = BargeInMonitor()
            TextToSpeech(answer, func=monitor, level_cb=self.audio_level.emit)
            monitor.close()
        else:
            TextToSpeech(answer, level_cb=self.audio_level.emit)
        self.audio_level.emit(0.0)
        self.state_changed.emit("available")
