"""
Centralized logging (Phase 6, see ENHANCEMENT_PLAN.md).

Replaces every ad-hoc `print(f"[ModuleName] ...")` across the codebase (a
few dozen of them, all with a hand-written "which module is this" prefix)
with the standard `logging` module: the same messages, but now with a real
level, a timestamp, and a second copy written to a rotating file under
Data/logs/ — so a failure that happened before the user thought to look
isn't gone the moment the console scrolls past it or the app is closed.

setup_logging() must run before anything else calls logging.getLogger(...)
.info/warning/error, or those calls fall through to logging's "handler of
last resort" (stderr, no formatting, no file copy). main.py calls it as
the literal first statement — ahead of even the onnxruntime/PyQt5 import-
order workaround already at the top of that file — because Backend.agent
(imported transitively the moment Frontend.GUI -> Backend.agent_worker
resolves) imports Backend.Database, which runs initialize_database() at
import time and logs the result.
"""

import logging
import logging.handlers
import os

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Data", "logs")
LOG_FILE = os.path.join(LOG_DIR, "jarvis.log")

_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    """Idempotent — safe to call more than once (e.g. from a test fixture
    that also wants file output); only the first call actually attaches
    handlers."""
    global _configured
    if _configured:
        return
    _configured = True

    os.makedirs(LOG_DIR, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    # 2MB * 3 backups is plenty for a desktop assistant's log volume and
    # keeps Data/logs/ from growing unbounded across a long-lived install.
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # Quiet noisy third-party HTTP client logging (Groq/edge-tts/DDG search
    # all go over httpx/httpcore/urllib3 under the hood) — otherwise every
    # network call logs a line at INFO and buries the app's own messages.
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
