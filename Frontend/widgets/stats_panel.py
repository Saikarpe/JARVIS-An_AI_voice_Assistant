"""
Usage stats panel (Phase 5.5, see ENHANCEMENT_PLAN.md).

Backend.Database.get_usage_summary() was written back in the original
schema and never called anywhere — ENHANCEMENT_PLAN.md P0-4 flagged the
dead import that used to sit at main.py:18 (Phase 0 already dropped that
import; this phase is the "surface it in the Phase 5 stats panel" half of
that note). It also needed Backend/agent.py to actually call log_usage()
(done alongside this), since nothing populated usage_stats either — this
panel would otherwise just show zeros.
"""

import logging

from PyQt5.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from Frontend import theme

logger = logging.getLogger(__name__)


class _StatTile(QFrame):
    def __init__(self, label: str, value: str, tokens: dict):
        super().__init__()
        layout = QVBoxLayout(self)
        value_label = QLabel(value)
        value_label.setStyleSheet(f"font-size: 26px; font-weight: 700; color: {tokens['accent']}; background: transparent;")
        caption = QLabel(label)
        caption.setStyleSheet(f"font-size: 12px; color: {tokens['text_dim']}; background: transparent;")
        layout.addWidget(value_label)
        layout.addWidget(caption)
        self.setStyleSheet(f"""
            _StatTile {{
                background-color: {tokens['surface_2']};
                border-radius: 10px;
                padding: 4px;
            }}
        """)


class StatsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Usage Stats")
        self.setMinimumSize(420, 420)
        self.setAccessibleName("Usage statistics dialog")

        t = theme.active()
        self.setStyleSheet(f"QDialog {{ background-color: {t['surface']}; }}")

        try:
            from Backend.Database import get_usage_summary
            summary = get_usage_summary()
        except Exception as e:
            summary = {"total_queries": 0, "successful": 0, "failed": 0,
                       "avg_response_time_ms": 0, "queries_by_type": {}}
            logger.warning("could not load usage summary: %s", e)

        root = QVBoxLayout(self)

        total = summary["total_queries"]
        success_rate = (summary["successful"] / total * 100) if total else 0.0

        tiles = QHBoxLayout()
        tiles.addWidget(_StatTile("Total queries", str(total), t))
        tiles.addWidget(_StatTile("Success rate", f"{success_rate:.0f}%", t))
        tiles.addWidget(_StatTile("Avg latency", f"{summary['avg_response_time_ms']:.0f} ms" if summary['avg_response_time_ms'] else "—", t))
        root.addLayout(tiles)

        root.addWidget(QLabel("By tool"))
        breakdown_area = QScrollArea()
        breakdown_area.setWidgetResizable(True)
        breakdown_widget = QWidget()
        breakdown_layout = QVBoxLayout(breakdown_widget)

        by_type = summary["queries_by_type"]
        if not by_type:
            empty = QLabel("No queries logged yet.")
            empty.setStyleSheet(f"color: {t['text_dim']};")
            breakdown_layout.addWidget(empty)
        else:
            max_count = max(by_type.values())
            for name, count in sorted(by_type.items(), key=lambda kv: kv[1], reverse=True):
                breakdown_layout.addWidget(_BarRow(name, count, max_count, t))
        breakdown_layout.addStretch(1)
        breakdown_area.setWidget(breakdown_widget)
        root.addWidget(breakdown_area, 1)


class _BarRow(QWidget):
    def __init__(self, name: str, count: int, max_count: int, tokens: dict):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        label = QLabel(name)
        label.setFixedWidth(140)
        label.setStyleSheet(f"color: {tokens['text']}; background: transparent;")

        bar_bg = QFrame()
        bar_bg.setFixedHeight(14)
        bar_bg.setStyleSheet(f"background-color: {tokens['surface_2']}; border-radius: 7px;")
        bar_layout = QHBoxLayout(bar_bg)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        fraction = count / max_count if max_count else 0
        fill = QFrame()
        fill.setStyleSheet(f"background-color: {tokens['accent']}; border-radius: 7px;")
        bar_layout.addWidget(fill, int(fraction * 100))
        bar_layout.addStretch(int((1 - fraction) * 100) or 1)

        count_label = QLabel(str(count))
        count_label.setFixedWidth(30)
        count_label.setStyleSheet(f"color: {tokens['text_dim']}; background: transparent;")

        layout.addWidget(label)
        layout.addWidget(bar_bg, 1)
        layout.addWidget(count_label)
