"""
Chat message rendering (Phase 5.2 / 5.3, see ENHANCEMENT_PLAN.md).

Replaces ChatSection's old single read-only QTextEdit, where every turn —
user, assistant, tool call — was an HTML <p> appended into one shared
document via a hand-rolled formatter that turned only the *first* "**...**"
pair in a message into <b> and left every other Markdown marker sitting in
the text as literal asterisks.

Each turn is now its own MessageBubble: real Markdown via
QTextEdit.setMarkdown() (Qt 5.14+ — handles headings, lists, bold, and
fenced code correctly instead of one bolded phrase), a body that grows to
fit its content instead of scrolling in a fixed box, and, for assistant
turns, a ToolTimeline that fills in live as Backend.agent.run_agent's
on_tool_start/on_tool_end callbacks fire (relayed through
Backend.agent_worker.AgentWorker's tool_started/tool_finished signals) —
so the agent's steps are visible above the answer as they happen, not
just the final text once everything is done.
"""

import datetime

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QTextCharFormat, QTextCursor
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

# Matches the 1920-baseline scale-factor sizing the rest of Frontend/GUI.py
# already uses (InitialScreen computes screen_width / 1920, etc.) — a
# percentage-of-scroll-area width would need to react to every resize
# event, which is a lot of plumbing for a value that only needs to look
# right, not track the window exactly.
MAX_BUBBLE_WIDTH = 620


def _tint_code_blocks(document, tokens):
    """QTextEdit.setMarkdown() already renders fenced/indented code blocks
    in a monospace face — Qt's Markdown importer sets
    QTextBlockFormat.setNonBreakableLines(True) on those blocks specifically
    to mark them as code, per the Qt docs — but it doesn't tint the
    background the way a code viewer normally does. This walks the
    finished document and fixes that up.

    A copy button *per code block* (rather than the one per-message copy
    button MessageBubble already has) would need embedding an interactive
    widget inside the text flow, which QTextEdit only supports through a
    custom QTextObjectInterface — a lot of machinery for a feature the
    per-message copy button already covers for the common case of "copy
    this whole answer". Skipped; call it out if a reviewer wants it.
    """
    bg = QColor(tokens["surface_2"])
    cursor = QTextCursor(document)
    block = document.begin()
    while block.isValid():
        if block.blockFormat().nonBreakableLines():
            cursor.setPosition(block.position())
            cursor.setPosition(
                block.position() + max(block.length() - 1, 0), QTextCursor.KeepAnchor
            )
            block_fmt = block.blockFormat()
            block_fmt.setBackground(bg)
            cursor.setBlockFormat(block_fmt)
            char_fmt = QTextCharFormat()
            char_fmt.setBackground(bg)
            char_fmt.setFontFamily("Consolas")
            cursor.mergeCharFormat(char_fmt)
        block = block.next()


class _AutoHeightTextEdit(QTextEdit):
    """A read-only QTextEdit that grows to fit its content instead of
    scrolling internally — a chat bubble's body is part of the page, not a
    text box with its own scrollbar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFrameStyle(QFrame.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setLineWrapMode(QTextEdit.WidgetWidth)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.document().documentLayout().documentSizeChanged.connect(self._resize_to_content)

    def _resize_to_content(self, *_args):
        margins = self.contentsMargins()
        height = self.document().size().height() + margins.top() + margins.bottom() + 4
        self.setFixedHeight(max(int(height), 20))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.document().setTextWidth(self.viewport().width())
        self._resize_to_content()


class ToolTimeline(QWidget):
    """Inline, collapsible list of tool calls for one assistant turn — the
    UI half of ENHANCEMENT_PLAN.md's Phase 5.3 mockup. Fed live by
    MessageBubble.tool_started()/tool_finished(), which AgentWorker's
    tool_started/tool_finished signals drive directly (one call per signal,
    no batching), so a call shows as pending the instant it starts rather
    than only once the whole turn is done.
    """

    ICON_PENDING = "\U0001F527"  # wrench
    ICON_DONE = "✅"  # check mark

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []  # list[QLabel], index-aligned with call order
        self._pending_index = {}  # tool name -> index of its latest un-finished row
        self._collapsed = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 4)
        outer.setSpacing(2)

        self._toggle = QPushButton()
        self._toggle.setFlat(True)
        self._toggle.setCursor(Qt.PointingHandCursor)
        self._toggle.clicked.connect(self._toggle_collapsed)
        outer.addWidget(self._toggle)

        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(8, 2, 0, 2)
        self._list_layout.setSpacing(2)
        outer.addWidget(self._list_widget)

        self.hide()  # nothing to show until the first tool call arrives

    def add_call(self, name: str, args: dict):
        arg_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
        label = QLabel(f"{self.ICON_PENDING} {name}({arg_str})")
        label.setWordWrap(True)
        self._list_layout.addWidget(label)
        self._rows.append(label)
        self._pending_index[name] = len(self._rows) - 1
        self._refresh_toggle_text()
        self.show()

    def mark_finished(self, name: str, result: str):
        idx = self._pending_index.pop(name, None)
        if idx is None or idx >= len(self._rows):
            return  # a tool_finished with no matching tool_started; ignore rather than crash
        self._rows[idx].setText(f"{self.ICON_DONE} {name}: {result}")

    def _refresh_toggle_text(self):
        n = len(self._rows)
        arrow = "▸" if self._collapsed else "▾"
        self._toggle.setText(f"{arrow} {n} tool call{'s' if n != 1 else ''}")

    def _toggle_collapsed(self):
        self._collapsed = not self._collapsed
        self._list_widget.setVisible(not self._collapsed)
        self._refresh_toggle_text()

    def apply_theme(self, tokens: dict):
        self._toggle.setStyleSheet(
            f"QPushButton {{ color: {tokens['text_dim']}; background: transparent; "
            f"border: none; text-align: left; font-size: 12px; padding: 0px; }}"
        )
        for label in self._rows:
            label.setStyleSheet(
                f"color: {tokens['text_dim']}; font-size: 12px; "
                f"font-family: Consolas, monospace; background: transparent;"
            )


class MessageBubble(QFrame):
    """One chat turn. role is 'user', 'assistant', or 'error' — 'error'
    reuses the assistant's left-aligned layout with the error token colour
    instead of getting a fourth alignment rule."""

    def __init__(self, role: str, author: str, parent=None):
        super().__init__(parent)
        self.role = role
        self._raw_text = ""
        self._tokens: dict | None = None
        self.setObjectName("MessageBubble")
        self.setMaximumWidth(MAX_BUBBLE_WIDTH)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.setAttribute(Qt.WA_Hover, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(8)
        self._author_label = QLabel(author)
        self._time_label = QLabel(datetime.datetime.now().strftime("%H:%M"))
        self._copy_btn = QPushButton("Copy")
        self._copy_btn.setFixedHeight(20)
        self._copy_btn.setCursor(Qt.PointingHandCursor)
        self._copy_btn.setAccessibleName(f"Copy {author} message")
        self._copy_btn.clicked.connect(self._copy_text)
        self._copy_btn.hide()  # revealed on hover, see enterEvent/leaveEvent

        header.addWidget(self._author_label)
        header.addWidget(self._time_label)
        header.addStretch(1)
        header.addWidget(self._copy_btn)
        outer.addLayout(header)

        self.timeline = ToolTimeline(self) if role == "assistant" else None
        if self.timeline is not None:
            outer.addWidget(self.timeline)

        self.body = _AutoHeightTextEdit(self)
        outer.addWidget(self.body)

    # ---- content ----
    def set_text(self, text: str):
        """Set (or replace) the bubble's full text. Used both for a
        one-shot message and to write the authoritative final answer over
        whatever append_token() streamed in, in case the two ever drift."""
        self._raw_text = text
        self.body.setMarkdown(text)
        if self._tokens is not None:
            _tint_code_blocks(self.body.document(), self._tokens)

    def append_token(self, token: str):
        """Re-parses the whole accumulated text as Markdown on every call.
        Wasteful for a very long streamed answer, but a streamed reply is
        at most a few hundred tokens and QTextEdit.setMarkdown() has no
        incremental / append API — re-parsing is the only option Qt
        offers, and it's fast enough at this size not to be visible."""
        self._raw_text += token
        self.body.setMarkdown(self._raw_text)
        if self._tokens is not None:
            _tint_code_blocks(self.body.document(), self._tokens)

    def tool_started(self, name: str, args: dict):
        if self.timeline is not None:
            self.timeline.add_call(name, args)

    def tool_finished(self, name: str, result: str):
        if self.timeline is not None:
            self.timeline.mark_finished(name, result)

    def _copy_text(self):
        QApplication.clipboard().setText(self._raw_text)

    # ---- hover-reveal copy button ----
    def enterEvent(self, event):
        self._copy_btn.show()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._copy_btn.hide()
        super().leaveEvent(event)

    # ---- theming ----
    def apply_theme(self, tokens: dict):
        self._tokens = tokens
        if self.role == "user":
            bg, fg = tokens["accent"], tokens["on_accent"]
        elif self.role == "error":
            bg, fg = tokens["surface_2"], tokens["error"]
        else:
            bg, fg = tokens["surface"], tokens["text"]

        self.setStyleSheet(
            f"#MessageBubble {{ background-color: {bg}; border-radius: 14px; "
            f"border: 1px solid {tokens['border']}; }}"
        )
        self.body.setStyleSheet(f"background: transparent; color: {fg}; font-size: 15px;")
        self._author_label.setStyleSheet(
            f"color: {fg}; font-weight: 600; font-size: 12px; background: transparent;"
        )
        self._time_label.setStyleSheet(
            f"color: {tokens['text_dim']}; font-size: 11px; background: transparent;"
        )
        self._copy_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {tokens['text_dim']}; "
            f"border: 1px solid {tokens['border']}; border-radius: 6px; font-size: 11px; "
            f"padding: 2px 8px; }}"
            f"QPushButton:hover {{ color: {fg}; border-color: {fg}; }}"
        )
        if self.timeline is not None:
            self.timeline.apply_theme(tokens)
        # Re-tint now that colours may have changed under an existing bubble
        # (a theme toggle mid-conversation) rather than only on the next edit.
        if self._raw_text:
            _tint_code_blocks(self.body.document(), tokens)
