"""
Shared configuration (Phase 4.4, see ENHANCEMENT_PLAN.md).

Started small and deliberately: the old code hardcoded the Groq model id
"llama-3.3-70b-versatile" in three different files, and Groq deprecated it
without warning — every chat path 404'd until this was found and fixed.
Centralizing it here means the next deprecation is a one-line fix, not a
grep-and-replace across the codebase.

As of Phase 4, the user-editable settings (wake word, STT backend, barge-in,
theme, proactive behaviors, ...) are a dataclass loaded from two layers:
  - .env           — install-time defaults, e.g. WAKE_WORD_ENABLED=false
                      on a machine with no working mic.
  - user_preferences (Backend.Database, a table that's existed since the
                      original schema with nothing ever writing to it) —
                      whatever the Phase 5.5 settings panel saves, and
                      always wins once a row exists, since that's the
                      user's actual current choice, made from a running app.

`settings` below is the one instance every other module imports and reads
from directly (`from Backend.config import settings`). There's no live
reload yet — the wake-word detector, TTS backend, etc. are all set up once
at startup from whatever `settings` held at import time, so a value the
settings panel writes only takes effect on next launch until something
subscribes to change notifications. That wiring is Phase 5.5's job, not
this one's.
"""

import dataclasses

from dotenv import dotenv_values

env_vars = dotenv_values(".env")

# Verified against `client.models.list()` on 2026-08-25 — supports both
# streaming and native tool calling (checked empirically, see git history
# for the verification script). If this ever 404s, re-run that check
# against Groq's current model list rather than guessing a replacement.
GROQ_CHAT_MODEL = "openai/gpt-oss-120b"

# Lighter/faster fallback, also confirmed to support tool calling.
GROQ_CHAT_MODEL_FAST = "openai/gpt-oss-20b"

# Whisper STT, used from Phase 3 onward.
GROQ_WHISPER_MODEL = "whisper-large-v3-turbo"


@dataclasses.dataclass
class Settings:
    # Phase 3 voice pipeline
    wake_word_enabled: bool = True
    wake_word_threshold: float = 0.5
    stt_backend: str = "groq"  # "groq" (Whisper) or "google" (free web API fallback)
    barge_in_enabled: bool = True
    mic_device_index: int = -1  # -1 = system default

    # Phase 5 UI
    theme: str = "dark"  # "dark" or "light"
    assistant_voice: str = ""  # edge-tts voice id; "" = fall back to .env AssistantVoice
    tts_rate: str = "+13%"  # edge-tts rate string, e.g. "+13%", "-10%"

    # Phase 4.3 proactive behaviors — both default OFF. An assistant that
    # talks unprompted at the wrong moment is worse than one that stays
    # quiet; see ENHANCEMENT_PLAN.md 4.3.
    proactive_enabled: bool = False
    morning_briefing_enabled: bool = False
    morning_briefing_time: str = "08:00"  # 24h "HH:MM", local time

    @classmethod
    def load(cls) -> "Settings":
        # Imported here, not at module level: Backend.Database does a
        # real sqlite connection + schema init on import, which every
        # single caller of Backend.config (including ones that only want
        # GROQ_CHAT_MODEL) would otherwise pay for, and — more importantly
        # — Backend.Database has no reason to import Backend.config back,
        # so keeping the dependency one-directional and lazy avoids ever
        # having to reason about import order between the two.
        from Backend.Database import get_preference

        defaults = cls()
        values = {}
        for field in dataclasses.fields(cls):
            default_value = getattr(defaults, field.name)
            env_raw = env_vars.get(field.name.upper())
            db_raw = get_preference(field.name)  # DB wins: it's the live, user-set value
            raw = db_raw if db_raw is not None else env_raw
            values[field.name] = (
                _coerce(raw, type(default_value)) if raw is not None else default_value
            )
        return cls(**values)

    def save(self):
        """Persist every field to user_preferences — called by the Phase
        5.5 settings panel after the user changes something."""
        from Backend.Database import set_preference

        for field in dataclasses.fields(self):
            set_preference(field.name, str(getattr(self, field.name)))


def _coerce(raw, target_type):
    if target_type is bool:
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if target_type is float:
        return float(raw)
    if target_type is int:
        return int(raw)
    return raw


settings = Settings.load()

# Backward-compatible module-level aliases. Backend/agent_worker.py and
# Backend/SpeechToText.py import these three names directly rather than
# `settings.<field>` — kept as plain constants (not properties) so those
# call sites don't need touching again until Phase 5.5 actually needs
# live reload, at which point `settings` itself (not these) is what a
# settings panel should mutate.
WAKE_WORD_ENABLED = settings.wake_word_enabled
STT_BACKEND = settings.stt_backend
BARGE_IN_ENABLED = settings.barge_in_enabled
