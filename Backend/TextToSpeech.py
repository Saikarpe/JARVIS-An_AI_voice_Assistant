import asyncio  # Async operations
import logging  # Structured logging (Phase 6)
import os  # File handling
import random  # Random responses
import threading  # Parallel stop monitoring

import edge_tts  # Text‑to‑speech engine
import numpy as np  # RMS envelope for the visualizer (Phase 5.4)
import pygame  # Handle audio playback
from dotenv import dotenv_values  # .env reader

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────────
# 🔧  Configuration
# ────────────────────────────────────────────────────────────────────────────────
env_vars = dotenv_values(".env")
# Voice ID for edge-tts, .env default. Falls back to a real voice id
# (rather than staying None) if AssistantVoice is unset/empty in .env —
# edge_tts.Communicate() requires a str voice, so with both this and
# Backend.config.settings.assistant_voice empty, TextToAudioFile() would
# otherwise pass None straight through and fail at the network call
# instead of at startup.
AssistantVoice = env_vars.get("AssistantVoice") or "en-US-JennyNeural"

# A global event used to interrupt playback from anywhere
_STOP_EVENT = threading.Event()

# ────────────────────────────────────────────────────────────────────────────────
# 📢  Core helpers (UNCHANGED PUBLIC APIS)
# ────────────────────────────────────────────────────────────────────────────────
async def TextToAudioFile(text) -> str:
    """Generate an MP3 file for *text* using edge‑tts and return its path."""
    file_path = r"Data\speech.mp3"

    # Ensure a clean slate
    if os.path.exists(file_path):
        os.remove(file_path)

    # Phase 5.5 settings panel: voice/rate are user_preferences-backed
    # (Backend.config.settings), same "no live reload, takes effect on next
    # launch" limitation documented in Backend/config.py's docstring. Lazy
    # import for the same reason Backend.config itself lazy-imports
    # Backend.Database — avoid paying for a DB connection on every module
    # that just wants a constant.
    from Backend.config import settings
    voice = settings.assistant_voice or AssistantVoice
    communicate = edge_tts.Communicate(text, voice, pitch="+5Hz", rate=settings.tts_rate)
    await communicate.save(file_path)
    return file_path


_LEVEL_CHUNK_MS = 50  # envelope resolution; matched against music.get_pos() below


def _build_level_envelope(file_path: str):
    """Decode *file_path* (already-init'd mixer format) into a list of
    0.0-1.0 RMS levels, one per _LEVEL_CHUNK_MS of audio (Phase 5.4).

    Deliberately reuses pygame.mixer.Sound's own decoder rather than adding
    an mp3-decoding dependency (ffmpeg/pydub) -- Backend/barge_in.py made
    the same call for the same reason (see its module docstring). Sound()
    decodes the whole file up front into the format pygame.mixer.init() was
    called with, which is exactly the raw samples _play_audio is about to
    play -- this is real output-signal RMS, not a synthesized approximation.
    Returns [] if decoding fails for any reason (missing mixer, unsupported
    format, ...); the caller treats that as "no level data" and the
    visualizer falls back to its idle animation, same fail-soft pattern as
    BargeInMonitor.
    """
    try:
        freq, fmt, channels = pygame.mixer.get_init()
        sound = pygame.mixer.Sound(file_path)
        raw = sound.get_raw()
        # fmt is a signed sample size in pygame's convention (e.g. -16 for
        # signed 16-bit little-endian); every mixer.init() default and every
        # value this project sets is 16-bit, so this covers what's in play.
        dtype = np.int16 if abs(fmt) == 16 else np.int8
        samples = np.frombuffer(raw, dtype=dtype).astype(np.float32)
        if channels > 1:
            usable = (samples.size // channels) * channels
            samples = samples[:usable].reshape(-1, channels)
        else:
            samples = samples.reshape(-1, 1)

        chunk_samples = max(1, int(freq * _LEVEL_CHUNK_MS / 1000))
        full_scale = float(np.iinfo(dtype).max)
        levels = []
        for start in range(0, samples.shape[0], chunk_samples):
            chunk = samples[start:start + chunk_samples]
            if chunk.size == 0:
                continue
            rms = float(np.sqrt(np.mean(chunk ** 2)))
            # Same empirical (not full-scale) ceiling as
            # Backend/SpeechToText.py's _frame_rms_level, scaled for the
            # sample width in use, so speech reliably reaches a visible bar
            # height instead of maxing out at a tiny fraction of 1.0.
            levels.append(max(0.0, min(1.0, rms / (full_scale * 0.25))))
        return levels
    except Exception as e:
        logger.warning("could not build level envelope, visualizer will idle during speech: %s", e)
        return []


def _play_audio(file_path: str, level_cb=None):
    """Internal: play *file_path* via pygame; exit early if _STOP_EVENT is set."""
    pygame.mixer.init()
    levels = _build_level_envelope(file_path) if level_cb else []
    try:
        pygame.mixer.music.load(file_path)
        pygame.mixer.music.play()
        clock = pygame.time.Clock()
        while pygame.mixer.music.get_busy():
            if _STOP_EVENT.is_set():
                pygame.mixer.music.stop()
                break
            if levels and level_cb:
                pos_ms = pygame.mixer.music.get_pos()  # ms since play() started, -1 if unknown
                if pos_ms >= 0:
                    idx = min(len(levels) - 1, pos_ms // _LEVEL_CHUNK_MS)
                    try:
                        level_cb(levels[idx])
                    except Exception:
                        pass
            clock.tick(10)  # 100 ms granularity
    finally:
        pygame.mixer.music.stop()
        pygame.mixer.quit()
        if os.path.exists(file_path):
            os.remove(file_path)
        if level_cb:
            try:
                level_cb(0.0)
            except Exception:
                pass


def TTS(Text, func=lambda r=None: True, level_cb=None):
    """Speak *Text* synchronously.

    If *func* is callable, it will be polled every 100 ms; returning False or
    raising an exception signals an early stop.  External code can also call
    `request_stop()` to cut speech immediately.

    level_cb, if given, is called with a 0.0-1.0 RMS level of the audio
    actually being played, roughly every 100ms (Phase 5.4) -- see
    _build_level_envelope() above. Backend.agent_worker.AgentWorker wires
    this to its audio_level signal so Frontend.GUI.CircularVisualizer
    pulses with the assistant's real voice instead of a synthetic wave.
    """
    # Reset interrupt state each time
    _STOP_EVENT.clear()

    # Background thread to watch *func* and raise the stop flag
    def _monitor():
        while not _STOP_EVENT.is_set():
            try:
                if callable(func):
                    # Some callbacks expect an arg, others none – handle both
                    try:
                        should_continue = func()
                    except TypeError:
                        should_continue = func(True)
                    if should_continue is False:
                        _STOP_EVENT.set()
                        break
            except Exception:
                _STOP_EVENT.set()
                break
            pygame.time.wait(100)

    try:
        file_path = asyncio.run(TextToAudioFile(Text))
        monitor_thread = threading.Thread(target=_monitor, daemon=True)
        monitor_thread.start()
        _play_audio(file_path, level_cb=level_cb)
        monitor_thread.join(timeout=0.2)
        return True
    except Exception as e:
        logger.error("error in TTS: %s", e)
        return False
    finally:
        _STOP_EVENT.clear()
        try:
            func(False)  # mirror original behaviour
        except Exception:
            pass


# ────────────────────────────────────────────────────────────────────────────────
# 🗣️  Public high‑level entry point (UNCHANGED SIGNATURE)
# ────────────────────────────────────────────────────────────────────────────────

def TextToSpeech(Text, func=lambda r=None: True, level_cb=None):
    """Speak *Text* in manageable chunks; keep original logic."""
    sentences = str(Text).split(".")

    responses = [
        "The rest of the result has been printed to the chat screen, kindly check it out sir.",
        "The rest of the text is now on the chat screen, sir, please check it.",
        "You can see the rest of the text on the chat screen, sir.",
        "The remaining part of the text is now on the chat screen, sir.",
        "Sir, you'll find more text on the chat screen for you to see.",
        "The rest of the answer is now on the chat screen, sir.",
        "Sir, please look at the chat screen, the rest of the answer is there.",
        "You'll find the complete answer on the chat screen, sir.",
        "The next part of the text is on the chat screen, sir.",
        "Sir, please check the chat screen for more information.",
        "There's more text on the chat screen for you, sir.",
        "Sir, take a look at the chat screen for additional text.",
        "You'll find more to read on the chat screen, sir.",
        "Sir, check the chat screen for the rest of the text.",
        "The chat screen has the rest of the text, sir.",
        "There's more to see on the chat screen, sir, please look.",
        "Sir, the chat screen holds the continuation of the text.",
        "You'll find the complete answer on the chat screen, kindly check it out sir.",
        "Please review the chat screen for the rest of the text, sir.",
        "Sir, look at the chat screen for the complete answer."
    ]

    if len(sentences) > 4 and len(Text) >= 250:
        preview = ". ".join(sentences[:2]).strip()
        TTS(f"{preview}. {random.choice(responses)}", func, level_cb=level_cb)
    else:
        TTS(Text, func, level_cb=level_cb)


# ────────────────────────────────────────────────────────────────────────────────
# 🔘  External control helpers
# ────────────────────────────────────────────────────────────────────────────────

def request_stop():
    """Call this from anywhere (e.g., when the user says 'stop') to interrupt speech."""
    _STOP_EVENT.set()


# Self‑test when run directly
if __name__ == "__main__":
    try:
        while True:
            txt = input("Enter the text: ")
            if txt.lower() in {"stop", "exit", "quit"}:
                request_stop()
                continue
            TextToSpeech(txt)
    except KeyboardInterrupt:
        pass