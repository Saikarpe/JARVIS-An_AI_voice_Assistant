"""
Toast notifications (Phase 5.5, see ENHANCEMENT_PLAN.md).

Before this, errors and reminders only ever went to a chat bubble (or, in
the case of most backend failures, a bare print() nobody sees unless they
have a console open) — easy to miss if the user isn't looking at the chat
pane, and reminders in particular are meant to interrupt whatever the user
is doing, not wait to be scrolled to.

ToastManager is a transparent overlay parented to MainWindow's central
widget, stacking small dismissable cards bottom-right and auto-closing them
after a timeout. It doesn't replace the chat bubble / error bubble — both
still happen — this is purely a "notice me now" layer on top.
"""

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from Frontend import theme

_ICONS = {"error": "⚠", "reminder": "⏰", "info": "ℹ"}


class _ToastCard(QWidget):
    def __init__(self, message: str, kind: str, on_close, parent=None):
        super().__init__(parent)
        # Stored and called through a wrapper (rather than
        # close_btn.clicked.connect(on_close) directly) so callers can
        # construct the card first and set the real close callback in a
        # separate statement — see ToastManager.show_toast(), which needs
        # the callback to close over the card it's attached to.
        self.on_close = on_close
        self.setAccessibleName(f"{kind.capitalize()} notification")
        t = theme.active()
        border = {"error": t["error"], "reminder": t["accent"], "info": t["accent_2"]}.get(kind, t["accent"])

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 8, 10)
        icon = QLabel(_ICONS.get(kind, "ℹ"))
        icon.setStyleSheet(f"font-size: 16px; color: {border}; background: transparent;")
        text = QLabel(message)
        text.setWordWrap(True)
        text.setStyleSheet(f"color: {t['text']}; background: transparent; font-size: 13px;")
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(22, 22)
        close_btn.setAccessibleName("Dismiss notification")
        close_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {t['text_dim']}; "
            f"border: none; font-size: 12px; padding: 0; }}"
            f"QPushButton:hover {{ color: {t['text']}; }}"
        )
        close_btn.clicked.connect(lambda: self.on_close())

        layout.addWidget(icon)
        layout.addWidget(text, 1)
        layout.addWidget(close_btn)

        self.setStyleSheet(f"""
            _ToastCard {{
                background-color: {t['surface_2']};
                border: 1px solid {border};
                border-left: 3px solid {border};
                border-radius: 8px;
            }}
        """)
        self.setFixedWidth(340)
        self.setMinimumHeight(0)


class ToastManager(QWidget):
    """Owns the stack of visible toasts. show_toast() is the only method
    callers need; everything else (positioning, auto-dismiss, stacking) is
    internal. Must be re-positioned by the parent's resizeEvent — see
    Frontend/GUI.py's MainWindow.resizeEvent()."""

    DEFAULT_DURATION_MS = 6000

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self._layout.addStretch(1)
        self._cards = []
        self.setFixedWidth(360)
        self.raise_()

    def show_toast(self, message: str, kind: str = "info", duration_ms: int | None = None):
        # Two statements, not one — card's own on_close closes over `card`,
        # which needs to already be a fully-assigned name to close over
        # (see _ToastCard.__init__'s docstring comment on self.on_close).
        card = _ToastCard(message, kind, on_close=lambda: None)
        card.on_close = lambda: self._remove(card)
        self._layout.insertWidget(self._layout.count() - 1, card)
        self._cards.append(card)
        card.show()
        self.reposition()
        self.raise_()
        QTimer.singleShot(duration_ms or self.DEFAULT_DURATION_MS, lambda: self._remove(card))

    def _remove(self, card):
        if card not in self._cards:
            return
        self._cards.remove(card)
        self._layout.removeWidget(card)
        card.deleteLater()
        self.reposition()

    def reposition(self):
        """Bottom-right corner of the parent, above the status bar."""
        parent = self.parentWidget()
        if parent is None:
            return
        self.adjustSize()
        margin = 20
        x = parent.width() - self.width() - margin
        y = parent.height() - self.height() - margin - 30  # clear the status bar
        self.move(max(0, x), max(0, y))
