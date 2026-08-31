"""
Settings panel (Phase 5.5, see ENHANCEMENT_PLAN.md).

Frontend/Graphics/Settings.png has existed since before Phase 0 and was
wired to nothing. This is the modal it was always meant to open: every
field here reads its starting value from Backend.config.settings and, on
Save, writes back through Settings.save() (Backend.Database.set_preference
under the hood — Phase 4.4's user_preferences table). Backend.config's own
docstring is explicit that most of these have no live-reload path yet —
wake word, STT backend, mic device, and barge-in are all read once at
import time by the modules that use them — so Save shows a "restart to
apply" notice rather than pretending everything takes effect immediately.
Theme is the one field that *does* apply live (MainWindow.apply_theme()
already exists and is cheap to call from here).
"""

import asyncio
import logging
import os
import tempfile

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from Frontend import theme

logger = logging.getLogger(__name__)

# A curated, static list rather than an edge_tts.list_voices() network
# fetch on dialog open — that call is a few hundred ms to a second against
# Microsoft's service, which is a bad trade for "the settings dialog pauses
# to open" against "one of a wide list of voices instead of these ten".
# Covers the languages/accents most likely to matter for a voice assistant
# demo; anyone who wants a different one can still set AssistantVoice in
# .env directly (that's still the fallback — see Backend/TextToSpeech.py).
VOICE_CHOICES = [
    ("en-US-JennyNeural", "Jenny (US English, female)"),
    ("en-US-GuyNeural", "Guy (US English, male)"),
    ("en-GB-SoniaNeural", "Sonia (British English, female)"),
    ("en-GB-RyanNeural", "Ryan (British English, male)"),
    ("en-IN-NeerjaNeural", "Neerja (Indian English, female)"),
    ("en-IN-PrabhatNeural", "Prabhat (Indian English, male)"),
    ("en-AU-NatashaNeural", "Natasha (Australian English, female)"),
]

RATE_CHOICES = ["-25%", "-10%", "+0%", "+13%", "+25%", "+40%"]


def _list_input_devices():
    """Returns [(index, label), ...], index -1 meaning system default.
    Fails soft to just the default entry if sounddevice/PortAudio can't
    enumerate anything (e.g. no audio subsystem at all in a CI sandbox)."""
    devices = [(-1, "System default")]
    try:
        import sounddevice as sd
        for idx, info in enumerate(sd.query_devices()):
            if info.get("max_input_channels", 0) > 0:
                devices.append((idx, info.get("name", f"Device {idx}")))
    except Exception as e:
        logger.warning("could not enumerate input devices: %s", e)
    return devices


class SettingsDialog(QDialog):
    def __init__(self, parent=None, on_theme_changed=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(460)
        self._on_theme_changed = on_theme_changed
        self.setAccessibleName("Settings dialog")

        from Backend.config import Settings
        self._settings = Settings.load()  # always the live values, not the process-start snapshot

        t = theme.active()
        self.setStyleSheet(f"QDialog {{ background-color: {t['surface']}; }}")

        root = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._build_voice_tab(), "Voice")
        tabs.addTab(self._build_general_tab(), "General")
        tabs.addTab(self._build_proactive_tab(), "Proactive")
        tabs.addTab(self._build_data_tab(), "Data")
        root.addWidget(tabs)

        note = QLabel("Some settings take effect after restarting Jarvis.")
        note.setStyleSheet(f"color: {t['text_dim']}; font-size: 11px;")
        root.addWidget(note)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setAccessibleName("Cancel settings")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Save")
        save_btn.setAccessibleName("Save settings")
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        root.addLayout(btn_row)

    # ── tabs ──
    def _build_voice_tab(self):
        w = QWidget()
        form = QFormLayout(w)
        s = self._settings

        self.mic_combo = QComboBox()
        self.mic_combo.setAccessibleName("Microphone device")
        devices = _list_input_devices()
        for idx, label in devices:
            self.mic_combo.addItem(label, idx)
        current = next((i for i, (idx, _) in enumerate(devices) if idx == s.mic_device_index), 0)
        self.mic_combo.setCurrentIndex(current)
        form.addRow("Microphone", self.mic_combo)

        voice_row = QHBoxLayout()
        self.voice_combo = QComboBox()
        self.voice_combo.setAccessibleName("Assistant voice")
        for voice_id, label in VOICE_CHOICES:
            self.voice_combo.addItem(label, voice_id)
        wanted = s.assistant_voice or VOICE_CHOICES[0][0]
        idx = next((i for i, (vid, _) in enumerate(VOICE_CHOICES) if vid == wanted), 0)
        self.voice_combo.setCurrentIndex(idx)
        preview_btn = QPushButton("▶ Preview")
        preview_btn.setAccessibleName("Preview voice")
        preview_btn.clicked.connect(self._preview_voice)
        voice_row.addWidget(self.voice_combo, 1)
        voice_row.addWidget(preview_btn)
        form.addRow("Voice", voice_row)

        self.rate_combo = QComboBox()
        self.rate_combo.setAccessibleName("Speech rate")
        self.rate_combo.addItems(RATE_CHOICES)
        rate_idx = RATE_CHOICES.index(s.tts_rate) if s.tts_rate in RATE_CHOICES else 3
        self.rate_combo.setCurrentIndex(rate_idx)
        form.addRow("Speech rate", self.rate_combo)

        form.addRow(_hr())

        self.wake_word_check = QCheckBox("Enabled")
        self.wake_word_check.setAccessibleName("Wake word enabled")
        self.wake_word_check.setChecked(s.wake_word_enabled)
        form.addRow("Wake word (\"hey jarvis\")", self.wake_word_check)

        self.wake_threshold = QSlider(Qt.Horizontal)
        self.wake_threshold.setAccessibleName("Wake word sensitivity")
        self.wake_threshold.setRange(10, 90)
        self.wake_threshold.setValue(int(s.wake_word_threshold * 100))
        form.addRow("Sensitivity", self.wake_threshold)

        self.barge_in_check = QCheckBox("Enabled")
        self.barge_in_check.setAccessibleName("Barge-in enabled")
        self.barge_in_check.setChecked(s.barge_in_enabled)
        form.addRow("Interrupt while speaking", self.barge_in_check)

        self.stt_combo = QComboBox()
        self.stt_combo.setAccessibleName("Speech-to-text backend")
        self.stt_combo.addItems(["groq", "google"])
        self.stt_combo.setCurrentText(s.stt_backend)
        form.addRow("STT backend", self.stt_combo)

        return w

    def _build_general_tab(self):
        w = QWidget()
        form = QFormLayout(w)
        self.theme_combo = QComboBox()
        self.theme_combo.setAccessibleName("Theme")
        self.theme_combo.addItems(["dark", "light"])
        self.theme_combo.setCurrentText(self._settings.theme)
        form.addRow("Theme", self.theme_combo)
        return w

    def _build_proactive_tab(self):
        w = QWidget()
        form = QFormLayout(w)
        s = self._settings

        self.proactive_check = QCheckBox("Enabled")
        self.proactive_check.setAccessibleName("Proactive behaviors enabled")
        self.proactive_check.setChecked(s.proactive_enabled)
        form.addRow("Proactive behaviors", self.proactive_check)

        self.briefing_check = QCheckBox("Enabled")
        self.briefing_check.setAccessibleName("Morning briefing enabled")
        self.briefing_check.setChecked(s.morning_briefing_enabled)
        form.addRow("Morning briefing", self.briefing_check)

        self.briefing_time = QLineEdit(s.morning_briefing_time)
        self.briefing_time.setAccessibleName("Morning briefing time")
        self.briefing_time.setPlaceholderText("HH:MM, 24h")
        form.addRow("Briefing time", self.briefing_time)

        return w

    def _build_data_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        info = QLabel(
            "Jarvis remembers facts about you across conversations (Phase 4.1). "
            "Clearing memory removes everything it has learned; it does not "
            "affect chat history."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        clear_btn = QPushButton("Clear long-term memory")
        clear_btn.setAccessibleName("Clear long-term memory")
        clear_btn.clicked.connect(self._clear_memory)
        layout.addWidget(clear_btn)
        layout.addStretch(1)
        return w

    # ── actions ──
    def _preview_voice(self):
        voice_id = self.voice_combo.currentData()
        rate = self.rate_combo.currentText()
        try:
            import edge_tts
            import pygame

            tmp_path = os.path.join(tempfile.gettempdir(), "jarvis_voice_preview.mp3")

            async def _synth():
                communicate = edge_tts.Communicate(
                    f"Hello, I'm {voice_id.split('-')[-1].replace('Neural', '')}.",
                    voice_id, pitch="+5Hz", rate=rate,
                )
                await communicate.save(tmp_path)

            asyncio.run(_synth())
            pygame.mixer.init()
            pygame.mixer.music.load(tmp_path)
            pygame.mixer.music.play()
        except Exception as e:
            QMessageBox.warning(self, "Preview failed", f"Could not preview that voice: {e}")

    def _clear_memory(self):
        confirm = QMessageBox.question(
            self, "Clear memory",
            "This permanently deletes everything Jarvis remembers about you. Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            from Backend.Database import clear_memories
            clear_memories()
            QMessageBox.information(self, "Memory cleared", "Long-term memory has been cleared.")
        except Exception as e:
            QMessageBox.warning(self, "Failed", f"Could not clear memory: {e}")

    def _save(self):
        s = self._settings
        s.mic_device_index = self.mic_combo.currentData()
        s.assistant_voice = self.voice_combo.currentData()
        s.tts_rate = self.rate_combo.currentText()
        s.wake_word_enabled = self.wake_word_check.isChecked()
        s.wake_word_threshold = self.wake_threshold.value() / 100.0
        s.barge_in_enabled = self.barge_in_check.isChecked()
        s.stt_backend = self.stt_combo.currentText()
        s.theme = self.theme_combo.currentText()
        s.proactive_enabled = self.proactive_check.isChecked()
        s.morning_briefing_enabled = self.briefing_check.isChecked()
        s.morning_briefing_time = self.briefing_time.text().strip() or "08:00"

        try:
            s.save()
        except Exception as e:
            QMessageBox.warning(self, "Save failed", f"Could not save settings: {e}")
            return

        if self._on_theme_changed:
            self._on_theme_changed(s.theme)

        self.accept()


def _hr():
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Sunken)
    return line
