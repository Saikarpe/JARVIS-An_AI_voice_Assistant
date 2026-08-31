"""
Conversation history sidebar (Phase 5.5, see ENHANCEMENT_PLAN.md).

Backend.Database.get_all_chat_history() has existed since the original
schema and returns every message across every session — useful for nothing
in particular, since it has no per-session grouping. Backend.Database.
get_sessions() (added alongside this) groups that into one row per session
(preview, message count, last-active time); this widget lists those rows
and lets the user click one to resume it — Backend.Database.set_session_id()
makes that session the *active* one, so continuing the conversation from
there actually appends to that session's history instead of a dead-end
read-only view.
"""

import logging

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from Frontend import theme

logger = logging.getLogger(__name__)


class HistorySidebar(QWidget):
    # emitted when the user picks a session to resume; str = session_id
    session_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAccessibleName("Conversation history")
        self.setFixedWidth(280)
        self._sessions = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        header = QHBoxLayout()
        title = QLabel("History")
        title.setStyleSheet("font-size: 15px; font-weight: 600; background: transparent;")
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setAccessibleName("Close history sidebar")
        close_btn.clicked.connect(self.hide)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(close_btn)
        layout.addLayout(header)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search conversations...")
        self.search_box.setAccessibleName("Search conversation history")
        self.search_box.textChanged.connect(self._filter)
        layout.addWidget(self.search_box)

        self.list_widget = QListWidget()
        self.list_widget.setAccessibleName("Past conversations")
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.list_widget, 1)

        self.retheme(theme.active())

    def refresh(self):
        try:
            from Backend.Database import get_sessions
            self._sessions = get_sessions(limit=100)
        except Exception as e:
            logger.warning("could not load sessions: %s", e)
            self._sessions = []
        self._render(self._sessions)

    def _render(self, sessions):
        self.list_widget.clear()
        for s in sessions:
            item = QListWidgetItem(f"{s['preview']}\n{s['message_count']} messages · {s['last_active']}")
            item.setData(Qt.UserRole, s["session_id"])
            self.list_widget.addItem(item)

    def _filter(self, text):
        text = text.strip().lower()
        if not text:
            self._render(self._sessions)
            return
        filtered = [s for s in self._sessions if text in s["preview"].lower()]
        self._render(filtered)

    def _on_item_clicked(self, item):
        session_id = item.data(Qt.UserRole)
        if session_id:
            self.session_selected.emit(session_id)

    def retheme(self, tokens: dict):
        self.setStyleSheet(f"""
            HistorySidebar {{
                background-color: {tokens['surface']};
                border-left: 1px solid {tokens['border']};
            }}
            QListWidget {{
                background-color: {tokens['surface']};
                border: none;
                color: {tokens['text']};
            }}
            QListWidget::item {{
                padding: 8px;
                border-bottom: 1px solid {tokens['border']};
            }}
            QListWidget::item:selected {{
                background-color: {tokens['accent']}33;
            }}
        """)
