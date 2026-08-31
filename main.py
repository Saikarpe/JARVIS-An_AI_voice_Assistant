"""
Entry point for Jarvis.

Phase 1 (see ENHANCEMENT_PLAN.md) replaced the old design here: a daemon
thread ran a listen/decide/act loop that communicated with the GUI purely by
writing files under Frontend/Files/, which the GUI polled with QTimers.
That's gone. Now:

  - Backend.agent_worker.AgentWorker holds the entire pipeline and runs on
    its own QThread, emitting Qt signals for every state change.
  - Backend.scheduler_worker.SchedulerWorker (Phase 4.2/4.3) runs on a
    second QThread, polling for due reminders and — if enabled — firing a
    daily proactive briefing.
  - Frontend.GUI.MainWindow.attach_worker()/attach_scheduler() wire those
    signals straight to the widgets that display them, and wire the GUI's
    own signals (typed queries, mic toggle, file upload) back to
    AgentWorker's slots.
  - This module's only job is to construct all of that and connect it.
"""

import os
import sys

# The Windows console's default codepage (cp1252 or the OEM codepage) can't
# encode plenty of characters this app prints in normal operation — search
# result text, an em dash in a log message, non-English translated speech.
# Found via a print() crashing mid-test with UnicodeEncodeError on
# character U+2192 in a debug line. Force UTF-8 on stdout/stderr with
# replacement instead of a hard crash for anything that still won't encode.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# Phase 6: configure logging before anything else logs. Backend.Database
# runs initialize_database() (which logs) at import time, and that import
# happens transitively the moment Frontend.GUI -> Backend.agent_worker ->
# Backend.agent resolves below — so this has to come before those imports,
# not just before main() runs. See Backend/logging_config.py's docstring.
from Backend.logging_config import setup_logging

setup_logging()

# Import onnxruntime (openwakeword's inference backend, see
# Backend/wake_word.py) before PyQt5 touches anything. Found by testing
# Phase 4: on Windows, whichever of PyQt5 or onnxruntime's native
# extension loads its DLLs into the process first "wins", and PyQt5
# imported first leaves onnxruntime's pybind11 extension unable to
# initialize ("DLL load failed") — reproduced with nothing more than
# `from PyQt5.QtWidgets import QApplication` before the onnxruntime
# import, no QApplication instance even required. WakeWordDetector
# already fails soft or this would be a hard crash instead of a silently
# disabled Phase 3 headline feature — but silently disabled is still a
# real regression, so it's fixed at the one place that's guaranteed to
# run before any PyQt5 import: the top of this file. Optional import
# (try/except) since onnxruntime is only needed for wake word, which
# already tolerates not being installed at all.
try:
    import onnxruntime  # noqa: F401
except ImportError:
    pass

import logging

from PyQt5.QtCore import QThread
from PyQt5.QtWidgets import QApplication

from Backend.agent_worker import AgentWorker
from Backend.scheduler_worker import SchedulerWorker
from Frontend.GUI import MainWindow

logger = logging.getLogger(__name__)


def main():
    app = QApplication(sys.argv)

    window = MainWindow()

    thread = QThread()
    worker = AgentWorker()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)

    window.attach_worker(worker)

    # Phase 4.2/4.3: a second QThread for reminders/proactive behaviors,
    # deliberately separate from AgentWorker's thread — SchedulerWorker's
    # 30s poll loop must keep running (and reminders must still fire) even
    # while AgentWorker is blocked inside a real, uninterruptible mic read.
    scheduler_thread = QThread()
    scheduler = SchedulerWorker()
    scheduler.moveToThread(scheduler_thread)
    scheduler_thread.started.connect(scheduler.run)

    window.attach_scheduler(scheduler)

    def _shutdown():
        worker.shutdown()
        scheduler.shutdown()
        thread.quit()
        scheduler_thread.quit()
        finished_cleanly = thread.wait(3000) and scheduler_thread.wait(3000)
        if not finished_cleanly:
            # The worker thread is very likely blocked inside a real,
            # uninterruptible microphone read (PortAudio/PyAudio has no
            # cooperative cancellation). Unlike the old daemon-thread
            # design, a QThread isn't auto-killed on process exit, so
            # without this the app could hang on close instead of exiting.
            # Force-terminate rather than risk that.
            logger.warning("a worker thread did not stop in time; forcing exit")
            os._exit(0)

    app.aboutToQuit.connect(_shutdown)

    thread.start()
    scheduler_thread.start()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
