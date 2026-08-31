"""
Shared pytest setup (Phase 6, see ENHANCEMENT_PLAN.md).

Sets JARVIS_DB_PATH (see Backend/Database.py) to a throwaway file *before*
anything else in this session gets a chance to import Backend.Database —
that module runs initialize_database() at import time, and pytest imports
each test file's top-level imports during collection, before any fixture
(including an autouse one) runs. A module-level statement in conftest.py
is the one thing guaranteed to execute before test collection touches any
test file, so this has to live here, unconditionally, not inside a
fixture function.

Every test in this suite therefore reads/writes a scratch SQLite file
under the OS temp dir — never the real Data/jarvis.db — and that file is
wiped at the start of each test session so a previous run's leftover
schema/data (SQLite doesn't have SQLAlchemy-style migration teardown)
never leaks into assertions about row counts.
"""

import os
import tempfile

_TEST_DB_PATH = os.path.join(tempfile.gettempdir(), "jarvis_pytest.db")
for _suffix in ("", "-wal", "-shm"):
    try:
        os.remove(_TEST_DB_PATH + _suffix)
    except OSError:
        pass
os.environ["JARVIS_DB_PATH"] = _TEST_DB_PATH

import pytest  # noqa: E402 — after the env var is set, not before


@pytest.fixture
def clean_registry(monkeypatch):
    """Isolates Backend.tools.registry's module-level _TOOLS dict for one
    test. _TOOLS is a process-wide singleton — Backend.agent does `import
    Backend.tools` at its own module load, registering every real tool —
    so a test that registers its own tools would either collide with a
    real tool's name or leak into later tests without this."""
    from Backend.tools import registry as registry_module

    monkeypatch.setattr(registry_module, "_TOOLS", {})
    return registry_module
