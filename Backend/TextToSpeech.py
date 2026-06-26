import pygame  # Handle audio playback
import random  # Random responses
import asyncio  # Async operations
import edge_tts  # Text‑to‑speech engine
import os  # File handling
import threading  # Parallel stop monitoring
from dotenv import dotenv_values  # .env reader

# ────────────────────────────────────────────────────────────────────────────────
# 🔧  Configuration
# ────────────────────────────────────────────────────────────────────────────────
env_vars = dotenv_values(".env")
AssistantVoice = env_vars.get("AssistantVoice")  # Voice ID for edge‑tts

# A global event used to interrupt playback from anywhere
_STOP_EVENT = threading.Event()

# ────────────────────────────────────────────────────────────────────────────────
# 📢  Core helpers (UNCHANGED PUBLIC APIS)
# ────────────────────────────────────────────────────────────────────────────────
async def TextToAudioFile(text) -> None:
    """Generate an MP3 file for *text* using edge‑tts and return its path."""
    file_path = r"Data\speech.mp3"

    # Ensure a clean slate
    if os.path.exists(file_path):
        os.remove(file_path)

    communicate = edge_tts.Communicate(text, AssistantVoice, pitch="+5Hz", rate="+13%")
    await communicate.save(file_path)
    return file_path


def _play_audio(file_path: str):
    """Internal: play *file_path* via pygame; exit early if _STOP_EVENT is set."""
    pygame.mixer.init()
    try:
        pygame.mixer.music.load(file_path)
        pygame.mixer.music.play()
        clock = pygame.time.Clock()
        while pygame.mixer.music.get_busy():
            if _STOP_EVENT.is_set():
                pygame.mixer.music.stop()
                break
            clock.tick(10)  # 100 ms granularity
    finally:
        pygame.mixer.music.stop()
        pygame.mixer.quit()
        if os.path.exists(file_path):
            os.remove(file_path)


def TTS(Text, func=lambda r=None: True):
    """Speak *Text* synchronously.

    If *func* is callable, it will be polled every 100 ms; returning False or
    raising an exception signals an early stop.  External code can also call
    `request_stop()` to cut speech immediately.
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
        _play_audio(file_path)
        monitor_thread.join(timeout=0.2)
        return True
    except Exception as e:
        print(f"Error in TTS: {e}")
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

def TextToSpeech(Text, func=lambda r=None: True):
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
        TTS(f"{preview}. {random.choice(responses)}", func)
    else:
        TTS(Text, func)


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