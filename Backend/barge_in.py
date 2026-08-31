"""
Barge-in (Phase 3.3, see ENHANCEMENT_PLAN.md): let the user interrupt
Jarvis by talking over it.

Backend/TextToSpeech.py's TTS() already had the right shape for this — it
takes a `func` callable, polls it every ~100ms while audio plays, and stops
playback the moment `func()` returns False. Before Phase 3 the only thing
that ever called request_stop() was the "stop" control phrase, and that
could only be recognized *after* the current TTS finished playing (nothing
was listening to the mic during playback). BargeInMonitor is a stateful
callable built for exactly TTS()'s `func` slot: each call reads one short
frame from the mic and runs it through webrtcvad; once enough continuous
voiced audio has been seen, it returns False.

This intentionally does NOT attempt to decode and stream edge-tts's MP3
output incrementally through a raw PCM output device — that needs a real
MP3 stream decoder (ffmpeg/pydub or similar), which is a lot of moving
parts for what's already a working, tested playback path. Interruptibility
is the acceptance criterion (see ENHANCEMENT_PLAN.md's Definition of Done,
#3); how the audio gets to the speaker isn't.
"""

import logging

import numpy as np
import sounddevice as sd
import webrtcvad

from Backend.config import settings

logger = logging.getLogger(__name__)

# Phase 5.5 settings panel — see Backend/SpeechToText.py's _MIC_DEVICE.
_MIC_DEVICE = settings.mic_device_index if settings.mic_device_index != -1 else None

SAMPLE_RATE = 16000
FRAME_MS = 30  # webrtcvad requires exactly 10, 20, or 30ms frames
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
DEFAULT_SUSTAINED_MS = 300  # how much continuous speech before we call it a real interruption


class BargeInMonitor:
    """A callable suitable for TextToSpeech.TTS()'s `func` argument.

    Opens the mic once (lazily, on first call) and keeps it open for the
    monitor's lifetime; each call reads one frame and classifies it. Fails
    soft: if the mic can't be opened at all, every call just returns True
    (never signals stop) so a missing/busy microphone disables barge-in
    instead of breaking normal speech playback.
    """

    def __init__(self, sustained_ms: int = DEFAULT_SUSTAINED_MS, vad_level: int = 2):
        self._vad = webrtcvad.Vad(vad_level)
        self._sustained_ms = sustained_ms
        self._voiced_ms = 0
        # sd.InputStream has no type stubs (ignore_missing_imports=true
        # makes it Any) — the explicit annotation here is what stops mypy
        # inferring self._stream's type as exactly None from this
        # assignment alone, which would make every later `self._stream.foo`
        # after a real reassignment a false "None has no attribute" error.
        self._stream: sd.InputStream | None = None
        self._failed = False
        # TTS() polls __call__ roughly every 100ms, coarser than a 30ms VAD
        # frame — draining only one VAD-sized frame per poll would leave the
        # rest sitting in the stream's buffer, growing a backlog over the
        # length of an utterance. Instead each poll drains *everything*
        # currently buffered into here, then classifies as many complete
        # 30ms frames as that yields, carrying any remainder to next time.
        self._pending = np.array([], dtype=np.int16)

    def _ensure_stream(self):
        if self._stream is not None or self._failed:
            return
        try:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME_SAMPLES,
                device=_MIC_DEVICE,
            )
            self._stream.start()
        except Exception as e:
            logger.warning("mic unavailable, barge-in disabled for this utterance: %s", e)
            self._failed = True

    def __call__(self, *_args, **_kwargs) -> bool:
        """Returns True to keep playing, False to signal TTS to stop."""
        self._ensure_stream()
        if self._stream is None:
            return True

        try:
            available = self._stream.read_available
            if available <= 0:
                return True
            new_samples, _overflowed = self._stream.read(available)
        except Exception:
            return True

        self._pending = np.concatenate([self._pending, new_samples.flatten()])

        while self._pending.size >= FRAME_SAMPLES:
            chunk, self._pending = self._pending[:FRAME_SAMPLES], self._pending[FRAME_SAMPLES:]
            try:
                is_speech = self._vad.is_speech(chunk.tobytes(), SAMPLE_RATE)
            except Exception:
                is_speech = False

            self._voiced_ms = self._voiced_ms + FRAME_MS if is_speech else 0
            if self._voiced_ms >= self._sustained_ms:
                self.close()
                return False
        return True

    def close(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
