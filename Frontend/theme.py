"""
Design tokens (Phase 5.1, see ENHANCEMENT_PLAN.md).

Before this, colours like #00D4FF and rgba(20, 20, 30, 0.9) were hardcoded
in a dozen separate setStyleSheet() calls across Frontend/GUI.py, which is
why the "Toggle Theme" button could only ever flip one top-level stylesheet
and leave everything else (the chat panel, the visualizer's own paintEvent
gradients, every button) exactly as dark as before. Two flat dicts with the
same keys, plus one function that turns a dict into a Qt stylesheet, is the
single source of truth every widget now reads from — see
MainWindow.apply_theme() in GUI.py, which is the only place either dict
gets touched.
"""

DARK = {
    "bg": "#0B0F14",
    "surface": "#141A22",
    "surface_2": "#1D2530",
    "border": "#2A3441",
    "text": "#E6EDF3",
    "text_dim": "#8B98A5",
    "accent": "#00D4FF",
    "accent_2": "#A050FF",
    "success": "#00E678",
    "warning": "#FFA028",
    "error": "#FF5C5C",
    "on_accent": "#04141A",  # text/icon colour placed *on top of* the accent colour
}

# Same keys as DARK. accent/success/warning/error are darkened from their
# DARK-mode values, not reused verbatim — #00D4FF on white is well under
# WCAG AA's 4.5:1 body-text contrast ratio, it only worked in DARK because
# it sat on a near-black surface.
#
# Phase 5.6 contrast audit (WCAG 2.1 relative-luminance formula, checked
# against every surface tone text_dim can sit on in each theme):
#   DARK  text_dim (#8B98A5) on bg/surface/surface_2:  6.53 / 5.94 / 5.25 : 1
#   LIGHT text_dim (#5B6672) on bg/surface/surface_2:  5.45 / 5.85 / 5.16 : 1
# All comfortably clear WCAG AA's 4.5:1 for normal-size body text.
LIGHT = {
    "bg": "#F5F7FA",
    "surface": "#FFFFFF",
    "surface_2": "#EDF1F5",
    "border": "#D7DEE5",
    "text": "#14181D",
    "text_dim": "#5B6672",
    "accent": "#0077A6",
    "accent_2": "#7A3FD1",
    "success": "#0B8A4C",
    "warning": "#9A5B00",
    "error": "#C22C22",
    "on_accent": "#FFFFFF",
}

_active_name = "dark"


def active() -> dict:
    """The currently active token dict. Widgets that need to re-theme
    themselves outside the global stylesheet cascade (MessageBubble,
    CircularVisualizer, CustomTopBar's custom paintEvent) read from this."""
    return DARK if _active_name == "dark" else LIGHT


def set_active(name: str):
    global _active_name
    _active_name = "light" if name == "light" else "dark"


def current_name() -> str:
    return _active_name


def stylesheet(tokens: dict | None = None) -> str:
    """A Qt stylesheet covering the generic widgets (QLineEdit, QPushButton,
    QLabel, scrollbars, ...) that don't need per-instance colour logic.
    Applied once, app-wide, via QApplication.setStyleSheet() in
    MainWindow.apply_theme() — widgets with role-dependent colour (a user
    bubble's accent background vs. an assistant bubble's surface
    background can't both be "QFrame { background: X }") keep their own
    apply_theme(tokens) instead; see Frontend/widgets/message_bubble.py.
    """
    t = tokens or active()
    return f"""
        QWidget {{
            color: {t['text']};
            font-family: 'Segoe UI', Arial, sans-serif;
        }}
        QMainWindow, QScrollArea, QStackedWidget {{
            background-color: {t['bg']};
        }}
        QLineEdit {{
            background-color: {t['surface']};
            color: {t['text']};
            border: 1px solid {t['border']};
            border-radius: 10px;
            padding: 8px 12px;
            font-size: 15px;
        }}
        QLineEdit:focus {{
            border: 1px solid {t['accent']};
        }}
        QPushButton {{
            background-color: {t['accent']};
            color: {t['on_accent']};
            border: none;
            border-radius: 10px;
            padding: 8px 16px;
            font-size: 14px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background-color: {t['accent_2']};
        }}
        QPushButton:disabled {{
            background-color: {t['surface_2']};
            color: {t['text_dim']};
        }}
        QLabel {{
            color: {t['text']};
            background-color: transparent;
        }}
        QStatusBar {{
            background-color: {t['surface']};
            color: {t['text_dim']};
        }}
        QMenu {{
            background-color: {t['surface_2']};
            color: {t['text']};
            border: 1px solid {t['border']};
        }}
        QMenu::item:selected {{
            background-color: {t['accent']};
            color: {t['on_accent']};
        }}
        QScrollBar:vertical {{
            background: {t['surface']};
            width: 10px;
            margin: 0px;
            border-radius: 5px;
        }}
        QScrollBar::handle:vertical {{
            background: {t['border']};
            min-height: 24px;
            border-radius: 5px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            background: none;
            height: 0px;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: none;
        }}
    """
