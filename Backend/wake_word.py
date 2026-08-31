"""
Wake-word detection (Phase 3.1, see ENHANCEMENT_PLAN.md).

Replaces the always-hot mic: the old FirstThread called SpeechRecognition()
in a tight loop, so the mic was live constantly and every stray noise burned
a full speech-to-text request. WakeWordDetector instead runs a small local
ONNX model (openWakeWord's "hey_jarvis") over short audio frames and only
signals "listening" once the wake word is actually heard.

Verified (2026-08-25): the model loads, runs inference, and correctly
scores synthetic noise at 0.0 (no false positive). Not verified: live
detection against a real spoken "hey jarvis" — that needs an actual
person speaking into a mic, which isn't something this session can do.
"""

import logging

import sounddevice as sd

from Backend.config import settings

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME_SAMPLES = 1280  # 80ms — openWakeWord's recommended chunk size
WAKEWORD_NAME = "hey_jarvis"
DEFAULT_THRESHOLD = 0.5
# Phase 5.5 settings panel — see Backend/SpeechToText.py's _MIC_DEVICE for
# why -1 maps to None (sounddevice's "system default" convention).
_MIC_DEVICE = settings.mic_device_index if settings.mic_device_index != -1 else None


class WakeWordDetector:
    """Wraps openWakeWord. Fails soft: if the model can't be loaded (no
    internet on first run to fetch it, no mic, import error), `available`
    is False and callers should fall back to always-on listening rather
    than crash — wake word is a UX improvement, not a hard dependency the
    user must have to launch the app."""

    def __init__(self, threshold: float = DEFAULT_THRESHOLD):
        self.threshold = threshold
        self.available = False
        self._model = None
        try:
            from openwakeword.model import Model
            from openwakeword.utils import download_models

            download_models([WAKEWORD_NAME])  # no-op if already downloaded
            self._model = Model(wakeword_models=[WAKEWORD_NAME], inference_framework="onnx")
            self.available = True
        except Exception as e:
            logger.warning("unavailable, falling back to always-on listening: %s", e)

    def listen(self, should_continue) -> bool:
        """Block until the wake word is heard, returning True — or until
        should_continue() returns False (checked every ~80ms, so the mic
        toggle / app shutdown can interrupt this quickly, unlike the old
        design's up-to-10-second blocking listen), returning False.
        """
        if not self.available:
            return False
        # available is only ever set True right after self._model is
        # successfully constructed (see __init__) — this assert documents
        # that invariant for mypy (self._model's declared type is Any |
        # None) and doubles as a real runtime check if it's ever violated.
        assert self._model is not None

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME_SAMPLES,
                device=_MIC_DEVICE,
            ) as stream:
                self._model.reset()
                while should_continue():
                    frame, _overflowed = stream.read(FRAME_SAMPLES)
                    scores = self._model.predict(frame.flatten())
                    if scores.get(WAKEWORD_NAME, 0.0) >= self.threshold:
                        return True
        except Exception as e:
            logger.warning("mic error during wake-word listen: %s", e)
        return False
