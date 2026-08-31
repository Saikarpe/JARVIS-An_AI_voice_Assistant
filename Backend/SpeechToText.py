"""
Speech-to-text (Phase 3.2, see ENHANCEMENT_PLAN.md).

Replaces the old `recognizer.recognize_google(audio)` call — an
undocumented, unauthenticated endpoint with no SLA and no punctuation —
with Groq's hosted Whisper. Since Backend/agent.py already talks to Groq,
this adds no new credential.

Capture itself moved from speech_recognition's sr.Microphone()/
recognizer.listen() (PyAudio-backed, blocking, and — found while testing
Phase 1 — capable of hanging in a way that outlives a bounded thread.wait())
to sounddevice + webrtcvad: short frames, VAD-based end-of-speech detection,
same low-level audio path Backend/wake_word.py and Backend/barge_in.py use.
speech_recognition is kept only as an STT *backend* option (Google's free
API, no credential needed) for offline-credential fallback — see
Backend.config.STT_BACKEND — re-using the audio this module already
captured rather than opening the mic a second time.
"""

import io
import logging
import wave

import mtranslate as mt
import numpy as np
import sounddevice as sd
import webrtcvad
from dotenv import dotenv_values

from Backend.config import GROQ_WHISPER_MODEL, STT_BACKEND, settings
from Backend.groq_client import get_groq_client

logger = logging.getLogger(__name__)

env_vars = dotenv_values(".env")
InputLanguage = env_vars.get("InputLanguage") or "en"

# Phase 5.5 settings panel: -1 means "system default", matching
# sounddevice's own convention of device=None for that. Read once at
# import time, same "no live reload" limitation as every other Settings
# field — see Backend/config.py.
_MIC_DEVICE = settings.mic_device_index if settings.mic_device_index != -1 else None

SAMPLE_RATE = 16000
FRAME_MS = 30  # webrtcvad requires exactly 10, 20, or 30ms frames
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 480
MAX_UTTERANCE_SECONDS = 15
SILENCE_MS_TO_END = 1200  # stop after this much continuous silence following speech
VAD_AGGRESSIVENESS = 2  # 0 (permissive) - 3 (aggressive filtering of non-speech)


def QueryModifier(Query):
    new_query = Query.lower().strip()
    question_words = ["how", "what", "who", "where", "when", "why", "which", "whose", "whom", "can you", "what's", "where's", "how's"]

    if any(word + " " in new_query for word in question_words):
        new_query = new_query.rstrip(".?!") + "?"
    else:
        new_query = new_query.rstrip(".?!") + "."

    return new_query.capitalize()

def UniversalTranslator(Text):
    english_translation = mt.translate(Text, "en", "auto")
    return english_translation.capitalize()


def _frame_rms_level(pcm: bytes) -> float:
    """RMS of one int16 PCM frame, normalized to roughly 0.0-1.0. Used to
    drive Frontend.GUI.CircularVisualizer.setLevel() (Phase 5.4) with the
    actual mic signal instead of the old math.sin()+random.uniform() fake.
    8000.0 is an empirical ceiling, not full-scale (32768) — normal speech
    RMS rarely gets near full-scale, so dividing by the true max would make
    the visualizer barely move."""
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(samples ** 2)))
    return max(0.0, min(1.0, rms / 8000.0))


def _capture_utterance_pcm(status_cb=None, level_cb=None):
    """Record one utterance using simple energy/VAD-based endpointing:
    starts once speech is detected, stops after SILENCE_MS_TO_END of
    continuous silence following speech, or after MAX_UTTERANCE_SECONDS
    regardless. Returns raw 16-bit mono PCM bytes at 16kHz, or None if no
    speech was detected at all within the timeout.

    80ms-granularity frame reads mean this can't hang the way the old
    blocking recognizer.listen(timeout=10) risked — every frame read
    returns quickly, so a caller polling should_continue-style logic
    around this (or just waiting out MAX_UTTERANCE_SECONDS) stays bounded.

    level_cb, if given, is called with a 0.0-1.0 RMS level for every frame
    read (voiced or not) — Backend.agent_worker.AgentWorker wires this to
    AgentWorker.audio_level, which Frontend.GUI.CircularVisualizer.setLevel
    reads (Phase 5.4).
    """
    def _status(s):
        if status_cb:
            status_cb(s)

    def _level(pcm):
        if level_cb:
            try:
                level_cb(_frame_rms_level(pcm))
            except Exception:
                pass

    vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
    frames = []
    started = False
    silence_frames = 0
    silence_limit = max(1, SILENCE_MS_TO_END // FRAME_MS)
    max_frames = max(1, MAX_UTTERANCE_SECONDS * 1000 // FRAME_MS)

    _status("listening")
    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME_SAMPLES, device=_MIC_DEVICE) as stream:
            for _ in range(max_frames):
                frame, _overflowed = stream.read(FRAME_SAMPLES)
                pcm = frame.tobytes()
                _level(pcm)
                try:
                    is_speech = vad.is_speech(pcm, SAMPLE_RATE)
                except Exception:
                    is_speech = False

                if is_speech:
                    started = True
                    silence_frames = 0
                    frames.append(pcm)
                elif started:
                    silence_frames += 1
                    frames.append(pcm)
                    if silence_frames >= silence_limit:
                        break
                # else: still waiting for speech to start — drop leading silence
    except Exception as e:
        logger.warning("mic error: %s", e)
        return None
    finally:
        _level(b"\x00\x00")  # drop the visualizer back to idle once capture stops

    if not started or not frames:
        return None
    return b"".join(frames)


def _pcm_to_wav_bytes(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # int16
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)
    return buf.getvalue()


def _transcribe_groq(pcm: bytes) -> str:
    wav_bytes = _pcm_to_wav_bytes(pcm)
    transcript = get_groq_client().audio.transcriptions.create(
        file=("audio.wav", wav_bytes),
        model=GROQ_WHISPER_MODEL,
        response_format="text",
        timeout=30,
    )
    return str(transcript).strip()


def _transcribe_google_fallback(pcm: bytes) -> str:
    """Re-uses the audio _capture_utterance_pcm already recorded — does
    NOT reopen the mic — and runs it through speech_recognition's free
    Google Web Speech API call, imported lazily so it's only a hard
    dependency when this fallback path actually runs."""
    import speech_recognition as sr
    audio = sr.AudioData(pcm, SAMPLE_RATE, 2)
    recognizer = sr.Recognizer()
    lang = "en-IN" if "en" in InputLanguage.lower() else InputLanguage
    return recognizer.recognize_google(audio, language=lang)


def SpeechRecognition(status_cb=None, level_cb=None):
    """Listen to the microphone and return the recognized text.

    status_cb, if given, is called with short status strings ("listening",
    "recognizing", "translating", "available") so the caller — normally
    Backend.agent_worker.AgentWorker — can relay them to the UI via a Qt
    signal. level_cb, if given, is called with a 0.0-1.0 mic RMS level per
    frame captured (Phase 5.4 visualizer).
    """
    def _status(s):
        if status_cb:
            status_cb(s)

    pcm = _capture_utterance_pcm(status_cb=status_cb, level_cb=level_cb)
    if not pcm:
        _status("available")
        return ""

    _status("recognizing")
    Text = ""
    try:
        if STT_BACKEND == "groq":
            Text = _transcribe_groq(pcm)
        else:
            Text = _transcribe_google_fallback(pcm)
    except Exception as e:
        logger.warning("%s transcription failed (%s), trying fallback", STT_BACKEND, e)
        try:
            Text = _transcribe_google_fallback(pcm)
        except Exception as e2:
            logger.warning("fallback transcription also failed: %s", e2)
            return ""

    if not Text or not Text.strip():
        return ""

    logger.info("Heard: %s", Text)

    if InputLanguage.lower() == "en" or "en" in InputLanguage.lower():
        return QueryModifier(Text)
    else:
        _status("translating")
        return QueryModifier(UniversalTranslator(Text))


# Run the speech recognition loop
if __name__ == "__main__":
    from Backend.logging_config import setup_logging
    setup_logging()
    while True:
        Text = SpeechRecognition(status_cb=lambda s: logger.info("status: %s", s))
        if Text:
            logger.info("You said: %s", Text)
