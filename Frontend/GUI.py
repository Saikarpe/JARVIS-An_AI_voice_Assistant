import logging
import math
import os
import random
import sys
import time

from dotenv import dotenv_values
from PyQt5.QtCore import QPointF, QSettings, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QBrush, QColor, QIcon, QKeySequence, QLinearGradient, QPainter, QPen, QPixmap, QRadialGradient
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QShortcut,
    QStackedWidget,
    QStatusBar,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from Frontend import theme
from Frontend.widgets.history_sidebar import HistorySidebar
from Frontend.widgets.message_bubble import MessageBubble
from Frontend.widgets.settings_dialog import SettingsDialog
from Frontend.widgets.stats_panel import StatsDialog
from Frontend.widgets.toast import ToastManager

logger = logging.getLogger(__name__)

env_vars = dotenv_values(".env")
Assistantname = env_vars.get("Assistantname") or "Grok"
current_dir = os.getcwd()
GraphicsDirPath = rf"{current_dir}\Frontend\Graphics"

# ────────────────────────────────────────────────────────────────────────────
# NOTE on architecture (Phase 1, see ENHANCEMENT_PLAN.md)
# ────────────────────────────────────────────────────────────────────────────
# This module used to talk to main.py by reading/writing flat files under
# Frontend/Files/ (Status.data, Responses.data, Mic.data, Query.data, ...),
# polled by three separate QTimers. That's gone. The GUI now only knows
# about state through Qt signals emitted by Backend.agent_worker.AgentWorker,
# wired up in MainWindow.attach_worker(). No file in Frontend/Files/ is read
# or written by this module any more.

def AnswerModifier(Answer):
    lines = Answer.split('\n')
    non_empty_lines = [line for line in lines if line.strip()]
    return '\n'.join(non_empty_lines)

def QueryModifier(Query):
    new_query = Query.lower().strip()
    query_words = new_query.split()
    question_words = ["how", "what", "who", "where", "when", "why", "which", "whose", "whom", "can you", "what's", "where's", "how's"]
    if any(word + " " in new_query for word in question_words):
        if query_words[-1][-1] in ['.', '?', '!']:
            new_query = new_query[:-1] + "?"
        else:
            new_query += "?"
    else:
        if query_words[-1][-1] in ['.', '?', '!']:
            new_query = new_query[:-1] + "."
        else:
            new_query += "."
    return new_query.capitalize()

def GraphicsDirectoryPath(Filename):
    return rf'{GraphicsDirPath}\{Filename}'


def _system_prefers_reduced_motion() -> bool:
    """Best-effort read of Windows' "Show animations" accessibility
    setting (Settings > Accessibility > Visual effects > Animation
    effects). Qt has no cross-platform prefers-reduced-motion query — this
    is deliberately Windows-only, matching this project's stated target
    platform (ENHANCEMENT_PLAN.md rule 6). Fails soft (assumes motion is
    fine) on any other OS or if the call fails, same pattern as
    WakeWordDetector/BargeInMonitor elsewhere in this codebase."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        SPI_GETCLIENTAREAANIMATION = 0x1042
        value = ctypes.c_bool()
        ok = ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(value), 0
        )
        return bool(ok) and not value.value
    except Exception:
        return False

class ChatSection(QWidget):
    """The chat panel (Phase 5.2/5.3, see ENHANCEMENT_PLAN.md).

    Used to be one QTextEdit that every turn got HTML-inserted into (see
    git history for the old addMessage()). Now a QScrollArea of
    MessageBubble widgets — one per turn — each independently
    Markdown-rendered, self-sizing, and (for assistant turns) carrying its
    own live ToolTimeline. Streaming and tool-call events target "the
    assistant bubble currently in progress for this turn", tracked by
    _current_bubble and reset to None whenever a new user turn starts or
    the current one's final answer arrives.
    """

    # GUI -> worker
    query_submitted = pyqtSignal(str)
    file_uploaded = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._current_bubble = None
        self._message_count = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 50, 20, 20)
        layout.setSpacing(10)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameStyle(QFrame.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setAccessibleName("Conversation")

        self._message_list = QWidget()
        self._message_layout = QVBoxLayout(self._message_list)
        self._message_layout.setContentsMargins(10, 10, 10, 10)
        self._message_layout.setSpacing(10)
        self._empty_state = self._build_empty_state()
        self._message_layout.addWidget(self._empty_state)
        self._message_layout.addStretch(1)  # keeps short conversations pinned to the top
        self.scroll_area.setWidget(self._message_list)
        layout.addWidget(self.scroll_area, 1)

        input_layout = QHBoxLayout()
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Type your query...")
        self.input_field.setAccessibleName("Message input")
        self.input_field.returnPressed.connect(self.send_query)
        send_button = QPushButton("Send")
        send_button.setAccessibleName("Send message")
        send_button.clicked.connect(self.send_query)
        upload_button = QPushButton("Upload File")
        upload_button.setAccessibleName("Upload file")
        upload_button.clicked.connect(self.upload_file)
        input_layout.addWidget(self.input_field)
        input_layout.addWidget(send_button)
        input_layout.addWidget(upload_button)
        layout.addLayout(input_layout)

    # ── slots: worker -> here ──
    @pyqtSlot(str)
    def on_user_message(self, message):
        self._current_bubble = None  # a new turn starts; any prior assistant bubble is done
        bubble = MessageBubble("user", "You")
        bubble.apply_theme(theme.active())
        bubble.set_text(message)
        self._add_bubble(bubble)

    @pyqtSlot(str)
    def on_token(self, token):
        self._ensure_assistant_bubble().append_token(token)
        self.scroll_to_bottom()

    @pyqtSlot(str)
    def on_response(self, message):
        # Authoritative final text — overwrites whatever on_token() streamed
        # in, in case the two ever drift (e.g. a response that arrived with
        # no preceding tokens, see AgentWorker._emit_initial_history).
        self._ensure_assistant_bubble().set_text(message)
        self._current_bubble = None  # turn over; the next token/tool call starts a fresh bubble
        self.scroll_to_bottom()

    @pyqtSlot(str, dict)
    def on_tool_started(self, name, args):
        self._ensure_assistant_bubble().tool_started(name, args)
        self.scroll_to_bottom()

    @pyqtSlot(str, str)
    def on_tool_finished(self, name, result):
        self._ensure_assistant_bubble().tool_finished(name, result)
        self.scroll_to_bottom()

    @pyqtSlot(str)
    def on_error(self, message):
        bubble = MessageBubble("error", "Error")
        bubble.apply_theme(theme.active())
        bubble.set_text(message)
        self._add_bubble(bubble)

    @pyqtSlot(str)
    def on_reminder(self, message):
        """Backend.scheduler_worker.SchedulerWorker.reminder_fired (Phase
        4.2). Deliberately its own bubble, not routed through
        _ensure_assistant_bubble() — a reminder can fire at any moment,
        including mid-stream of an unrelated AgentWorker turn, and
        shouldn't get folded into (or reset) that turn's in-progress
        bubble state."""
        bubble = MessageBubble("assistant", Assistantname)
        bubble.apply_theme(theme.active())
        bubble.set_text(f"⏰ Reminder: {message}")
        self._add_bubble(bubble)

    @pyqtSlot(str)
    def on_briefing(self, text):
        """Backend.scheduler_worker.SchedulerWorker.briefing_fired (Phase 4.3)."""
        bubble = MessageBubble("assistant", Assistantname)
        bubble.apply_theme(theme.active())
        bubble.set_text(text)
        self._add_bubble(bubble)

    # ── empty state (Phase 5.5) ──
    def _build_empty_state(self) -> QWidget:
        """Shown when there's genuinely nothing in the conversation — a
        fresh install with no history, or after Ctrl+L clears the current
        session. Before this the pane was just blank; see
        ENHANCEMENT_PLAN.md 5.5."""
        examples = [
            "What's the weather like today?",
            "Search the web for the latest AI news",
            "Remind me to take a break in 20 minutes",
        ]
        w = QWidget()
        w.setAccessibleName("Empty conversation state")
        layout = QVBoxLayout(w)
        layout.setContentsMargins(20, 40, 20, 20)
        layout.setSpacing(12)
        title = QLabel(f"Ask {Assistantname} anything")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 20px; font-weight: 600; background: transparent;")
        layout.addWidget(title)
        subtitle = QLabel("Try one of these to get started:")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("background: transparent;")
        layout.addWidget(subtitle)
        for example in examples:
            btn = QPushButton(example)
            btn.setAccessibleName(f"Example query: {example}")
            btn.clicked.connect(lambda _checked=False, q=example: self.query_submitted.emit(q))
            layout.addWidget(btn)
        return w

    def _update_empty_state(self):
        self._empty_state.setVisible(self._message_count == 0)

    def clear_messages(self):
        """Removes every message row (Ctrl+L / switching to a different
        history session) and restores the empty state."""
        while self._message_layout.count() > 2:  # empty_state + trailing stretch stay
            item = self._message_layout.takeAt(1)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._current_bubble = None
        self._message_count = 0
        self._update_empty_state()

    def load_history(self, entries):
        """Replays a list of {"role", "content"} dicts (Backend.Database.
        get_chat_history's shape) into the pane — used when the history
        sidebar switches the active session."""
        self.clear_messages()
        for entry in entries:
            if entry["role"] == "user":
                self.on_user_message(entry["content"])
            elif entry["role"] == "assistant":
                self.on_response(entry["content"])

    # ── internals ──
    def _ensure_assistant_bubble(self) -> MessageBubble:
        if self._current_bubble is None:
            self._current_bubble = MessageBubble("assistant", Assistantname)
            self._current_bubble.apply_theme(theme.active())
            self._add_bubble(self._current_bubble)
        return self._current_bubble

    def _add_bubble(self, bubble: MessageBubble):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        if bubble.role == "user":
            row.addStretch(1)
            row.addWidget(bubble)
        else:
            row.addWidget(bubble)
            row.addStretch(1)
        row_widget = QWidget()
        row_widget.setLayout(row)
        # Insert before the trailing stretch (always the last item), so new
        # rows land at the bottom instead of after it.
        self._message_layout.insertWidget(self._message_layout.count() - 1, row_widget)
        self._message_count += 1
        self._update_empty_state()
        self.scroll_to_bottom()

    def scroll_to_bottom(self):
        bar = self.scroll_area.verticalScrollBar()
        # The scroll area hasn't re-laid-out yet at the moment a bubble is
        # added/edited, so bar.maximum() would still be the *old* max — defer
        # one event-loop tick so layout has actually happened first.
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    def retheme(self, tokens: dict):
        """Called by MainWindow.apply_theme() on every existing bubble —
        the global QApplication stylesheet only covers widgets with no
        role-dependent colour, which a bubble's background is (see
        Frontend/theme.py's stylesheet() docstring)."""
        for i in range(self._message_layout.count() - 1):  # skip the trailing stretch
            row_widget = self._message_layout.itemAt(i).widget()
            if row_widget is None:
                continue
            for bubble in row_widget.findChildren(MessageBubble):
                bubble.apply_theme(tokens)

    # ── GUI -> worker ──
    def send_query(self):
        query = self.input_field.text().strip()
        if query:
            modified_query = QueryModifier(query)
            self.input_field.clear()
            self.query_submitted.emit(modified_query)

    def upload_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select File", "", "All Files (*);;Images (*.png *.jpg);;PDFs (*.pdf)")
        if file_path:
            bubble = MessageBubble("user", "You")
            bubble.apply_theme(theme.active())
            bubble.set_text(f"📎 Uploaded: {os.path.basename(file_path)}")
            self._add_bubble(bubble)
            self.file_uploaded.emit(file_path)

class CircularVisualizer(QWidget):
    """A circular audio visualizer with animated bars, glowing orb, and state-driven colors.

    Phase 5.4 (see ENHANCEMENT_PLAN.md): bar heights used to come purely
    from math.sin() + random.uniform() — decorative, not connected to any
    real audio. setLevel() now feeds it the actual signal: mic RMS during
    listening (Backend/SpeechToText.py), decoded TTS-output RMS during
    speaking (Backend/TextToSpeech.py), both routed through
    AgentWorker.audio_level. There's no per-band spectrum here (that would
    need either a multi-band FFT streamed off the mic or a raw PCM output
    path — out of scope, see Backend/barge_in.py's docstring for the same
    call on the TTS side) — one scalar RMS level is distributed across the
    ring with a per-bar phase offset so it still reads as motion rather
    than a single pulsing blob. If no level has arrived recently (state has
    no audio behind it — thinking, tool, idle), bars fall back to the
    original gentle synthetic animation instead of sitting frozen.
    """
    NUM_BARS = 64
    _LEVEL_TIMEOUT_S = 0.4  # no fresh level within this long -> treat as silence, use synthetic idle motion
    STATE_COLORS = {
        'listening':  (0, 212, 255),     # Cyan
        'recognizing': (0, 212, 255),
        'thinking':   (160, 80, 255),    # Purple
        'searching':  (255, 160, 40),    # Orange
        'speaking':   (0, 230, 120),     # Green
        'available':  (40, 100, 130),    # Dim cyan
    }

    def __init__(self, size=350, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WA_TranslucentBackground)

        # Bar heights (0.0 – 1.0)
        self._bars = [0.0] * self.NUM_BARS
        self._target_bars = [0.0] * self.NUM_BARS
        self._state = 'available'
        self._phase = 0.0          # animation phase counter
        self._pulse = 0.0          # pulse ring radius offset
        self._pulse_dir = 1
        self._level = 0.0          # last real audio_level received (Phase 5.4)
        self._last_level_ts = 0.0
        self._reduced_motion = _system_prefers_reduced_motion()  # Phase 5.6

        # 30 FPS animation timer
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick)
        self._anim_timer.start(33)

    # ----- public API -----
    def setState(self, status_text: str):
        """Map a status string (from AgentWorker.state_changed) to an internal state key."""
        t = status_text.lower()
        for key in self.STATE_COLORS:
            if key in t:
                self._state = key
                return
        self._state = 'available'

    def setLevel(self, level: float):
        """Feed a real 0.0-1.0 RMS audio level (Phase 5.4). Connected to
        AgentWorker.audio_level in MainWindow.attach_worker()."""
        self._level = max(0.0, min(1.0, level))
        self._last_level_ts = time.monotonic()

    # ----- internal animation -----
    def _tick(self):
        if self._reduced_motion:
            # Phase 5.6: OS-level "reduce animations" respected — a static
            # ring at a fixed height, still recoloured per state (state is
            # never conveyed by colour alone here either; see setState()'s
            # caller, which also updates the status label text), just no
            # motion. No pulse, no per-bar variation.
            for i in range(self.NUM_BARS):
                self._bars[i] = 0.22
            self.update()
            return

        self._phase += 0.06
        # Pulse ring
        self._pulse += 0.4 * self._pulse_dir
        if self._pulse > 12 or self._pulse < -4:
            self._pulse_dir *= -1

        active = self._state not in ('available',)
        level_is_fresh = (time.monotonic() - self._last_level_ts) < self._LEVEL_TIMEOUT_S

        # Generate target bar heights based on state
        for i in range(self.NUM_BARS):
            if active and level_is_fresh:
                # Real signal: one scalar RMS level spread around the ring
                # with a per-bar phase offset (see class docstring — there's
                # no real per-band spectrum data available), so a sustained
                # tone still reads as motion rather than a static ring.
                wave = 0.5 + 0.5 * math.sin(self._phase * 2.5 + i * 0.35)
                self._target_bars[i] = max(0.05, min(1.0, self._level * (0.6 + 0.4 * wave)))
            elif active:
                # No fresh level (e.g. "thinking"/"tool" — nothing is
                # actually making sound) — original synthetic wave.
                wave = 0.35 + 0.35 * math.sin(self._phase * 2.5 + i * 0.35)
                noise = random.uniform(-0.12, 0.12)
                self._target_bars[i] = max(0.08, min(1.0, wave + noise))
            else:
                # Gentle idle breathing
                self._target_bars[i] = 0.08 + 0.06 * math.sin(self._phase + i * 0.15)

        # Smooth interpolation
        for i in range(self.NUM_BARS):
            self._bars[i] += (self._target_bars[i] - self._bars[i]) * 0.18

        self.update()

    # ----- painting -----
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        cx = self._size / 2
        cy = self._size / 2
        r, g, b = self.STATE_COLORS.get(self._state, (0, 212, 255))

        # ---- 1. Outer glow ----
        glow_rad = self._size * 0.48
        glow = QRadialGradient(QPointF(cx, cy), glow_rad)
        glow.setColorAt(0.0, QColor(r, g, b, 25))
        glow.setColorAt(0.6, QColor(r, g, b, 8))
        glow.setColorAt(1.0, QColor(r, g, b, 0))
        p.setBrush(QBrush(glow))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(cx, cy), glow_rad, glow_rad)

        # ---- 2. Pulse ring ----
        pulse_r = self._size * 0.30 + self._pulse
        pen = QPen(QColor(r, g, b, 50), 1.5)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), pulse_r, pulse_r)

        # ---- 3. Bars ----
        inner_radius = self._size * 0.18
        max_bar_len = self._size * 0.18
        bar_width_deg = 360.0 / self.NUM_BARS

        for i in range(self.NUM_BARS):
            angle_deg = i * bar_width_deg
            angle_rad = math.radians(angle_deg)
            h = self._bars[i] * max_bar_len

            x1 = cx + inner_radius * math.cos(angle_rad)
            y1 = cy + inner_radius * math.sin(angle_rad)
            x2 = cx + (inner_radius + h) * math.cos(angle_rad)
            y2 = cy + (inner_radius + h) * math.sin(angle_rad)

            # Alpha gradient: brighter for taller bars
            alpha = int(100 + 155 * self._bars[i])
            pen = QPen(QColor(r, g, b, alpha), 2.5, Qt.SolidLine, Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # ---- 4. Inner glowing orb ----
        orb_r = self._size * 0.14
        orb_grad = QRadialGradient(QPointF(cx, cy), orb_r)
        orb_grad.setColorAt(0.0, QColor(r, g, b, 90))
        orb_grad.setColorAt(0.5, QColor(r, g, b, 40))
        orb_grad.setColorAt(1.0, QColor(r, g, b, 10))
        p.setBrush(QBrush(orb_grad))
        p.setPen(QPen(QColor(r, g, b, 60), 1))
        p.drawEllipse(QPointF(cx, cy), orb_r, orb_r)

        # ---- 5. Thin inner ring ----
        p.setPen(QPen(QColor(r, g, b, 80), 1.0))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), inner_radius - 2, inner_radius - 2)

        p.end()


class InitialScreen(QWidget):
    # GUI -> worker
    mic_toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        desktop = QApplication.desktop()
        screen_width = desktop.screenGeometry().width()
        screen_height = desktop.screenGeometry().height()
        scale_factor = min(screen_width / 1920, screen_height / 1080)
        self._scale_factor = scale_factor

        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, int(100 * scale_factor))

        # ---- Circular Audio Visualizer ----
        vis_size = int(380 * scale_factor)
        self.visualizer = CircularVisualizer(size=vis_size)

        # ---- Mic toggle button ----
        self.icon_label = QLabel()
        pixmap = QPixmap(GraphicsDirectoryPath('Mic_on.png'))
        new_pixmap = pixmap.scaled(int(80 * scale_factor), int(80 * scale_factor), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.icon_label.setPixmap(new_pixmap)
        self.icon_label.setFixedSize(int(100 * scale_factor), int(100 * scale_factor))
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.mic_enabled = True  # mic starts ON, matching prior default behavior
        self._render_mic_icon()
        # A standard, working PyQt5 idiom (wire an event handler onto a
        # plain QLabel instance instead of subclassing it just for this) —
        # mypy's method-assign check is right that overwriting a method in
        # general is unsound, but for a Qt event-handler slot specifically,
        # this is the normal pattern, not a mistake.
        self.icon_label.mousePressEvent = self.toggle_icon  # type: ignore[method-assign]

        self.label = QLabel("")

        content_layout.addStretch(1)
        content_layout.addWidget(self.visualizer, alignment=Qt.AlignCenter)
        content_layout.addWidget(self.label, alignment=Qt.AlignCenter)
        content_layout.addWidget(self.icon_label, alignment=Qt.AlignCenter)
        content_layout.addStretch(1)
        self.setLayout(content_layout)
        # No more setFixedHeight/Width(screen_*) here (Phase 5.5, see
        # ENHANCEMENT_PLAN.md "Window behavior") — QStackedWidget already
        # sizes each page to fill whatever the actual window is, and a
        # fixed size tied to the *desktop* resolution fought a resizable
        # window that's smaller (or larger, across a monitor change) than
        # that.
        self.retheme(theme.active())

    def paintEvent(self, event):
        t = theme.active()
        painter = QPainter(self)
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0, QColor(t["bg"]))
        gradient.setColorAt(1, QColor(t["surface"]))
        painter.fillRect(self.rect(), gradient)
        super().paintEvent(event)

    def retheme(self, tokens: dict):
        self.icon_label.setStyleSheet(f"""
            background-color: {tokens['accent']}33;
            border-radius: 50px;
            padding: 10px;
        """)
        self.label.setStyleSheet(f"""
            color: {tokens['accent']};
            font-size: 18px;
            font-family: 'Segoe UI', Arial;
            margin-bottom: 20px;
            background: transparent;
        """)
        self.update()

    # ── slot: worker -> here ──
    @pyqtSlot(str)
    def on_status(self, status):
        self.label.setText(status)
        self.visualizer.setState(status)

    def _render_mic_icon(self):
        icon_name = 'Mic_on.png' if self.mic_enabled else 'Mic_off.png'
        pixmap = QPixmap(GraphicsDirectoryPath(icon_name))
        size = int(80 * self._scale_factor)
        self.icon_label.setPixmap(pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def toggle_icon(self, event=None):
        self.mic_enabled = not self.mic_enabled
        self._render_mic_icon()
        self.mic_toggled.emit(self.mic_enabled)

class MessageScreen(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        # No setFixedHeight/Width(screen_*) — see InitialScreen's __init__
        # for why (Phase 5.5, resizable window).
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.chat_section = ChatSection()
        layout.addWidget(self.chat_section)
        self.setLayout(layout)

    def paintEvent(self, event):
        t = theme.active()
        painter = QPainter(self)
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0, QColor(t["bg"]))
        gradient.setColorAt(1, QColor(t["surface"]))
        painter.fillRect(self.rect(), gradient)
        super().paintEvent(event)

class CustomTopBar(QWidget):
    def __init__(self, parent: "MainWindow", stacked_widget):
        super().__init__(parent)
        # Stored separately from Qt's own self.parent() (which every method
        # below used to call directly): self.parent() is typed to return
        # the *base* QObject | None — correct for an arbitrary QWidget, but
        # every call site here needs the concrete MainWindow methods/
        # attributes (toggle_history_sidebar, apply_theme, ...), which
        # QObject doesn't have. parent is always actually a MainWindow (see
        # every construction site of this class), so this is a real type,
        # not a cast papering over a mismatch.
        self._main_window = parent
        self.stacked_widget = stacked_widget
        self.initUI()

    def initUI(self):
        self.setFixedHeight(60)
        layout = QHBoxLayout(self)
        layout.setAlignment(Qt.AlignRight)
        layout.setContentsMargins(10, 5, 10, 5)

        # Home/Chat/Theme get their look for free from the app-wide
        # QPushButton rule in Frontend.theme.stylesheet() (set in
        # MainWindow.apply_theme()) — no per-button stylesheet needed, and
        # a theme toggle now actually re-colours them, unlike before.
        self.title_label = QLabel(f" {str(Assistantname).capitalize()} AI ")
        self.title_label.setObjectName("TopBarTitle")

        home_button = QPushButton(" Home")
        home_button.setIcon(QIcon(GraphicsDirectoryPath("Home.png")))
        home_button.setAccessibleName("Home screen")
        home_button.clicked.connect(lambda: self.stacked_widget.setCurrentIndex(0))

        message_button = QPushButton(" Chat")
        message_button.setIcon(QIcon(GraphicsDirectoryPath("Chats.png")))
        message_button.setAccessibleName("Chat screen")
        message_button.clicked.connect(lambda: self.stacked_widget.setCurrentIndex(1))

        theme_button = QPushButton("Toggle Theme")
        theme_button.setAccessibleName("Toggle theme")
        theme_button.clicked.connect(self.toggle_theme)

        # Phase 5.5: the three panels that used to have no way in —
        # Settings.png existed and was wired to nothing, get_usage_summary()
        # was imported and never called, and the history table had a
        # get_all_chat_history() nobody displayed grouped by session.
        history_button = QPushButton(" History")
        history_button.setAccessibleName("Toggle conversation history")
        history_button.clicked.connect(lambda: self._main_window.toggle_history_sidebar())

        stats_button = QPushButton(" Stats")
        stats_button.setAccessibleName("Usage statistics")
        stats_button.clicked.connect(lambda: self._main_window.open_stats())

        settings_button = QPushButton()
        settings_button.setIcon(QIcon(GraphicsDirectoryPath("Settings.png")))
        settings_button.setAccessibleName("Open settings")
        settings_button.clicked.connect(lambda: self._main_window.open_settings())

        fullscreen_button = QPushButton("⛶")
        fullscreen_button.setAccessibleName("Toggle fullscreen")
        fullscreen_button.clicked.connect(lambda: self._main_window.toggle_fullscreen())

        # Window-control buttons keep a distinct "danger" red rather than
        # the accent colour — that's intentional, not left over from the
        # old hardcoded styling, so it's still tokens['error'] rather than
        # a literal #FF4C4C.
        self.minimize_button = QPushButton()
        self.minimize_button.setIcon(QIcon(GraphicsDirectoryPath('Minimize2.png')))
        self.minimize_button.setAccessibleName("Minimize window")
        self.minimize_button.clicked.connect(self.minimizeWindow)

        self.maximize_button = QPushButton()
        self.maximize_icon = QIcon(GraphicsDirectoryPath('Maximize.png'))
        self.restore_icon = QIcon(GraphicsDirectoryPath('Minimize.png'))
        self.maximize_button.setIcon(self.maximize_icon)
        self.maximize_button.setAccessibleName("Maximize window")
        self.maximize_button.clicked.connect(self.maximizeWindow)

        self.close_button = QPushButton()
        self.close_button.setIcon(QIcon(GraphicsDirectoryPath('Close.png')))
        self.close_button.setAccessibleName("Close window")
        self.close_button.clicked.connect(self.closeWindow)

        layout.addWidget(self.title_label)
        layout.addStretch(1)
        layout.addWidget(home_button)
        layout.addWidget(message_button)
        layout.addWidget(history_button)
        layout.addWidget(stats_button)
        layout.addWidget(settings_button)
        layout.addWidget(theme_button)
        layout.addWidget(fullscreen_button)
        layout.addSpacing(10)
        layout.addWidget(self.minimize_button)
        layout.addWidget(self.maximize_button)
        layout.addWidget(self.close_button)

        self.draggable = True
        self.offset = None
        self.retheme(theme.active())

    def paintEvent(self, event):
        t = theme.active()
        painter = QPainter(self)
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0, QColor(t["surface_2"]))
        gradient.setColorAt(1, QColor(t["bg"]))
        painter.fillRect(self.rect(), gradient)
        super().paintEvent(event)

    def retheme(self, tokens: dict):
        self.title_label.setStyleSheet(f"""
            color: {tokens['accent']};
            font-size: 20px;
            font-family: 'Segoe UI', Arial;
            font-weight: bold;
            background: transparent;
        """)
        danger_style = f"""
            QPushButton {{
                background-color: {tokens['error']};
                border-radius: 15px;
                padding: 8px;
            }}
            QPushButton:hover {{
                background-color: {tokens['warning']};
            }}
        """
        for btn in (self.minimize_button, self.maximize_button, self.close_button):
            btn.setStyleSheet(danger_style)
        self.update()

    def toggle_theme(self):
        next_name = "light" if theme.current_name() == "dark" else "dark"
        self._main_window.apply_theme(next_name)

    def minimizeWindow(self):
        self._main_window.showMinimized()

    def maximizeWindow(self):
        if self._main_window.isMaximized():
            self._main_window.showNormal()
            self.maximize_button.setIcon(self.maximize_icon)
        else:
            self._main_window.showMaximized()
            self.maximize_button.setIcon(self.restore_icon)

    def closeWindow(self):
        self._main_window.close()

    def mousePressEvent(self, event):
        if self.draggable:
            self.offset = event.pos()

    def mouseMoveEvent(self, event):
        if self.draggable and self.offset:
            new_pos = event.globalPos() - self.offset
            self._main_window.move(new_pos)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._worker = None
        self._force_quit = False  # set by the tray's Quit action / Ctrl+Q; see closeEvent()
        # Always set for real by _setup_tray() before this window can ever
        # process a close event — declared here (rather than relying on
        # getattr(self, "tray", None) at the one place it's read) so mypy
        # knows the attribute genuinely always exists by then.
        self.tray: QSystemTrayIcon | None = None
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.initUI()

    def initUI(self):
        self.stacked_widget = QStackedWidget(self)
        self.initial_screen = InitialScreen()
        self.message_screen = MessageScreen()
        self.stacked_widget.addWidget(self.initial_screen)
        self.stacked_widget.addWidget(self.message_screen)

        top_bar = CustomTopBar(self, self.stacked_widget)
        self.setMenuWidget(top_bar)

        # Phase 5.5: the history sidebar docks alongside the stacked pages
        # rather than floating, so it doesn't cover the visualizer/chat.
        central = QWidget()
        central_layout = QHBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self.stacked_widget, 1)
        self.history_sidebar = HistorySidebar()
        self.history_sidebar.hide()
        self.history_sidebar.session_selected.connect(self._on_session_selected)
        central_layout.addWidget(self.history_sidebar)
        self.setCentralWidget(central)

        # Named _status_bar, not statusBar — QMainWindow already has a
        # statusBar() *method*; an instance attribute of the same name
        # shadows it (Python allows this, so it silently "worked", but
        # mypy correctly flags every use as calling methods QMainWindow's
        # own statusBar() return type doesn't have — this ambiguity is
        # exactly what the name collision causes).
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        # A frameless window (Qt.FramelessWindowHint, above) gets no native
        # resize border from Windows — QStatusBar's built-in QSizeGrip is
        # what makes this "a normal resizable window" (ENHANCEMENT_PLAN.md
        # 5.5) rather than the old fixed-fullscreen one: drag the bottom-
        # right corner. It's the one resize handle, not full edge-dragging,
        # but it's a real, working one for a modest amount of code.
        self._status_bar.setSizeGripEnabled(True)
        self._status_bar.showMessage("Ready")

        self.toasts = ToastManager(self)

        self._top_bar = top_bar
        self.apply_theme(theme.current_name())

        self._restore_geometry()
        self._setup_tray()
        self._setup_shortcuts()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "toasts"):
            self.toasts.reposition()

    # ── window geometry (Phase 5.5 "Window behavior") ──
    def _restore_geometry(self):
        qsettings = QSettings("Jarvis", "JarvisAssistant")
        saved = qsettings.value("window_geometry")
        if saved is not None:
            self.restoreGeometry(saved)
        else:
            self.resize(1200, 800)
            screen = QApplication.desktop().screenGeometry()
            self.move((screen.width() - self.width()) // 2, (screen.height() - self.height()) // 2)

    def _save_geometry(self):
        qsettings = QSettings("Jarvis", "JarvisAssistant")
        qsettings.setValue("window_geometry", self.saveGeometry())

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # ── system tray (Phase 5.5) ──
    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = None
            return
        icon = QIcon(GraphicsDirectoryPath("Mic_on.png"))
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip(f"{Assistantname} — running")
        menu = QMenu()
        show_action = QAction("Show Jarvis", self)
        show_action.triggered.connect(self._restore_from_tray)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self._quit_from_tray)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self._restore_from_tray()

    def _restore_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self):
        self._force_quit = True
        self.close()

    def closeEvent(self, event):
        """The X button minimizes to the tray instead of quitting — a
        wake-word assistant is meant to keep listening in the background
        (ENHANCEMENT_PLAN.md 5.5), and the old behavior (forced fullscreen,
        closing = quitting) didn't support that at all. The tray's own
        "Quit" action and Ctrl+Q set _force_quit first to actually exit."""
        if self.tray is not None and not self._force_quit:
            event.ignore()
            self.hide()
            self.tray.showMessage(
                f"{Assistantname} is still running",
                "Minimized to the system tray. Right-click the tray icon to quit.",
                QSystemTrayIcon.Information, 3000,
            )
            return
        self._save_geometry()
        event.accept()

    # ── keyboard shortcuts (Phase 5.5) ──
    def _setup_shortcuts(self):
        # member= positional (not activated= keyword) — both work identically
        # at runtime, but PyQt5-stubs' QShortcut overloads only model the
        # positional/member= form; activated= is real PyQt5 API its stubs
        # just don't cover, which mypy would otherwise flag on every line.
        QShortcut(QKeySequence("Ctrl+Space"), self, self._toggle_mic)
        QShortcut(QKeySequence("Ctrl+K"), self, self.open_command_palette)
        QShortcut(QKeySequence("Esc"), self, self._stop_speaking)
        QShortcut(QKeySequence("Ctrl+L"), self, self._clear_conversation)
        QShortcut(QKeySequence("Ctrl+,"), self, self.open_settings)
        QShortcut(QKeySequence("Ctrl+Q"), self, self._quit_from_tray)
        QShortcut(QKeySequence("F11"), self, self.toggle_fullscreen)

    def _toggle_mic(self):
        """Ctrl+Space. Not true hold-to-talk (that would need a keyPress/
        keyRelease pair rather than a QShortcut, plus a way to interrupt an
        in-progress blocking mic read) — toggles the same mic-enabled state
        the InitialScreen icon does, which is the toggle model the rest of
        this app already uses."""
        self.initial_screen.toggle_icon()

    def _stop_speaking(self):
        if self._worker is not None:
            self._worker.stop_speaking()

    def _clear_conversation(self):
        confirm = QMessageBox.question(
            self, "Clear conversation",
            "Clear the current conversation? This can't be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            from Backend.Database import clear_chat_history
            clear_chat_history()
        except Exception as e:
            logger.warning("could not clear chat history: %s", e)
        self.message_screen.chat_section.clear_messages()

    def open_command_palette(self):
        """Ctrl+K. Grounded in the agent's actual registered tools
        (Backend.tools.registry) rather than a fixed list of made-up
        example commands — picking one drops a template into the input box
        for editing rather than invoking the tool directly, since most
        tools take arguments only the user can supply."""
        try:
            from Backend.tools.registry import get_schemas
            schemas = get_schemas()
        except Exception as e:
            logger.warning("could not load tool registry: %s", e)
            schemas = []

        dialog = QDialog(self)
        dialog.setWindowTitle("Command Palette")
        dialog.setMinimumWidth(420)
        layout = QVBoxLayout(dialog)
        list_widget = QListWidget()
        list_widget.setAccessibleName("Available commands")
        for schema in schemas:
            fn = schema["function"]
            item = QListWidgetItem(f"{fn['name']} — {fn['description']}")
            item.setData(Qt.UserRole, fn["name"])
            list_widget.addItem(item)
        layout.addWidget(list_widget)

        def _pick(item):
            self.stacked_widget.setCurrentIndex(1)
            field = self.message_screen.chat_section.input_field
            field.setText(f"Use {item.data(Qt.UserRole)} to ")
            field.setFocus()
            dialog.accept()

        list_widget.itemDoubleClicked.connect(_pick)
        dialog.exec_()

    # ── settings / history / stats (Phase 5.5) ──
    def open_settings(self):
        SettingsDialog(self, on_theme_changed=self.apply_theme).exec_()

    def open_stats(self):
        StatsDialog(self).exec_()

    def toggle_history_sidebar(self):
        if self.history_sidebar.isVisible():
            self.history_sidebar.hide()
        else:
            self.history_sidebar.refresh()
            self.history_sidebar.show()

    def _on_session_selected(self, session_id):
        try:
            from Backend.Database import get_chat_history, set_session_id
            set_session_id(session_id)
            history = get_chat_history(session_id=session_id, limit=200)
        except Exception as e:
            logger.warning("could not switch session: %s", e)
            return
        self.message_screen.chat_section.load_history(history)
        self.stacked_widget.setCurrentIndex(1)
        self.toasts.show_toast("Resumed a past conversation.", kind="info")

    def apply_theme(self, name: str):
        """The single place either of Frontend.theme's token dicts gets
        touched (Phase 5.1, see ENHANCEMENT_PLAN.md). Sets the app-wide
        stylesheet for widgets with no role-dependent colour, then asks
        every widget that *does* have one (a user bubble's accent
        background vs. an assistant bubble's surface background can't
        both come from one global "QFrame { background: X }" rule) to
        re-theme itself directly."""
        theme.set_active(name)
        tokens = theme.active()
        # isinstance, not "is not None" — QApplication.instance() is typed
        # via its base class QCoreApplication (a stub limitation, not a
        # real ambiguity: main.py only ever constructs a QApplication), and
        # setStyleSheet() is QApplication-specific, not on QCoreApplication.
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setStyleSheet(theme.stylesheet(tokens))
        self.message_screen.chat_section.retheme(tokens)
        self.initial_screen.retheme(tokens)
        self._top_bar.retheme(tokens)
        if hasattr(self, "history_sidebar"):
            self.history_sidebar.retheme(tokens)
        self.update()

    def attach_worker(self, worker):
        """Wire an AgentWorker's signals to this window's widgets, and this
        window's user-initiated signals back to the worker's slots. This is
        the only place file-based IPC used to happen — now it's all Qt
        signal/slot connections.

        worker -> GUI connections below are left as Qt.AutoConnection
        (queued, since the worker lives on its own QThread): that's safe and
        correct because the GUI thread's event loop (app.exec_() in
        main.py) is always running to dispatch them.

        GUI -> worker connections are forced to Qt.DirectConnection. The
        worker's run() is a plain blocking Python loop, not
        QThread.exec_(), so it never pumps a Qt event loop itself — a
        queued connection into it would sit in that thread's event queue
        forever and never be delivered. DirectConnection instead runs the
        slot synchronously on the *calling* (GUI) thread, which is fine
        here because every one of these slots only does a thread-safe
        queue.Queue.put() or a plain attribute write (atomic under the
        GIL for the bool/str values involved) — see agent_worker.py.
        """
        self._worker = worker
        chat = self.message_screen.chat_section
        initial = self.initial_screen

        worker.state_changed.connect(initial.on_status)
        worker.state_changed.connect(self._status_bar.showMessage)
        worker.audio_level.connect(initial.visualizer.setLevel)
        worker.user_message.connect(chat.on_user_message)
        worker.token.connect(chat.on_token)
        worker.response_done.connect(chat.on_response)
        worker.tool_started.connect(chat.on_tool_started)
        worker.tool_finished.connect(chat.on_tool_finished)
        worker.error.connect(chat.on_error)
        worker.error.connect(lambda msg: self.toasts.show_toast(msg, kind="error"))

        # PyQt5-stubs' pyqtBoundSignal.connect() overloads don't model the
        # (slot, Qt.ConnectionType) form at all — real PyQt5/sip supports
        # it (that's the whole DirectConnection mechanism explained in this
        # method's docstring above), the stub is just incomplete here.
        chat.query_submitted.connect(worker.handle_text_query, Qt.DirectConnection)  # type: ignore[call-arg]
        chat.file_uploaded.connect(worker.handle_file_uploaded, Qt.DirectConnection)  # type: ignore[call-arg]
        initial.mic_toggled.connect(worker.set_mic_enabled, Qt.DirectConnection)  # type: ignore[call-arg]

    def attach_scheduler(self, scheduler):
        """Wire a Backend.scheduler_worker.SchedulerWorker's signals to the
        chat panel (Phase 4.2/4.3). One-directional (scheduler -> GUI) —
        the scheduler has no slots the GUI needs to call, unlike
        attach_worker()'s AgentWorker, so there's no DirectConnection
        half here."""
        chat = self.message_screen.chat_section
        scheduler.reminder_fired.connect(chat.on_reminder)
        scheduler.briefing_fired.connect(chat.on_briefing)
        scheduler.reminder_fired.connect(
            lambda msg: self.toasts.show_toast(f"Reminder: {msg}", kind="reminder")
        )
        scheduler.briefing_fired.connect(
            lambda _text: self.toasts.show_toast("Your morning briefing is ready.", kind="info")
        )

def GraphicalUserInterface():
    """Standalone launcher used only for manual UI testing (no worker
    attached — nothing will actually respond). main.py builds and wires
    the real app (QApplication + MainWindow + AgentWorker on a QThread)."""
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    GraphicalUserInterface()
