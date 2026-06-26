from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QTextEdit, QStackedWidget, QWidget, QLineEdit,
    QVBoxLayout, QHBoxLayout, QPushButton, QFrame, QLabel, QSizePolicy,
    QFileDialog, QMenu, QStatusBar
)
from PyQt5.QtGui import (
    QIcon, QPainter, QMovie, QColor, QTextCharFormat, QFont, QPixmap,
    QLinearGradient, QRadialGradient, QPen, QBrush, QConicalGradient
)
from PyQt5.QtCore import Qt, QSize, QTimer, QRect, QPointF
from dotenv import dotenv_values
import sys
import os
import uuid
import math
import random

env_vars = dotenv_values(".env")
Assistantname = env_vars.get("Assistantname", "Grok")
current_dir = os.getcwd()
old_chat_message = ""
TempDirPath = rf"{current_dir}\Frontend\Files"
GraphicsDirPath = rf"{current_dir}\Frontend\Graphics"

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

def SetMicrophoneStatus(Command):
    with open(rf'{TempDirPath}\Mic.data', "w", encoding='utf-8') as file:
        file.write(Command)

def GetMicrophoneStatus():
    with open(rf'{TempDirPath}\Mic.data', "r", encoding='utf-8') as file:
        return file.read()

def SetAssistantStatus(Status):
    with open(rf'{TempDirPath}\Status.data', "w", encoding='utf-8') as file:
        file.write(Status)

def GetAssistantStatus():
    with open(rf'{TempDirPath}\Status.data', "r", encoding='utf-8') as file:
        return file.read()

def MicButtonInitialed():
    SetMicrophoneStatus("False")

def MicButtonClosed():
    SetMicrophoneStatus("True")

def GraphicsDirectoryPath(Filename):
    return rf'{GraphicsDirPath}\{Filename}'

def TempDirectoryPath(Filename):
    return rf'{TempDirPath}\{Filename}'

def ShowTextToScreen(Text):
    with open(rf'{TempDirPath}\Responses.data', "w", encoding='utf-8') as file:
        file.write(Text)

class ChatSection(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 50, 20, 120)
        layout.setSpacing(10)

        self.chat_text_edit = QTextEdit()
        self.chat_text_edit.setReadOnly(True)
        self.chat_text_edit.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.chat_text_edit.setFrameStyle(QFrame.NoFrame)
        self.chat_text_edit.setStyleSheet("""
            background-color: rgba(20, 20, 30, 0.9);
            color: #00D4FF;
            border-radius: 10px;
            padding: 15px;
            font-family: 'Arial';
            font-size: 18px;
        """)
        layout.addWidget(self.chat_text_edit)

        font = QFont("Arial", 18, QFont.Weight.Medium)
        self.chat_text_edit.setFont(font)
        self.chat_text_edit.setContextMenuPolicy(Qt.CustomContextMenu)
        self.chat_text_edit.customContextMenuRequested.connect(self.show_context_menu)

        self.typing_label = QLabel()
        typing_movie = QMovie(GraphicsDirectoryPath('Typing.gif'))
        typing_movie.setScaledSize(QSize(50, 50))
        self.typing_label.setMovie(typing_movie)
        self.typing_label.setAlignment(Qt.AlignRight)
        self.typing_label.hide()
        layout.addWidget(self.typing_label)

        input_layout = QHBoxLayout()
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Type your query...")
        self.input_field.setStyleSheet("""
            background-color: rgba(20, 20, 30, 0.9);
            color: #00D4FF;
            border-radius: 10px;
            padding: 8px;
            font-family: 'Arial';
            font-size: 18px;
        """)
        send_button = QPushButton("Send")
        send_button.setStyleSheet("""
            QPushButton {
                background-color: #00D4FF;
                color: black;
                border-radius: 10px;
                padding: 8px;
                font-family: 'Arial';
            }
            QPushButton:hover {
                background-color: #00BFFF;
            }
        """)
        send_button.clicked.connect(self.send_query)
        upload_button = QPushButton("Upload File")
        upload_button.setStyleSheet("""
            QPushButton {
                background-color: #00D4FF;
                color: black;
                border-radius: 10px;
                padding: 8px;
                font-family: 'Arial';
            }
            QPushButton:hover {
                background-color: #00BFFF;
            }
        """)
        upload_button.clicked.connect(self.upload_file)
        input_layout.addWidget(self.input_field)
        input_layout.addWidget(send_button)
        input_layout.addWidget(upload_button)
        layout.addLayout(input_layout)

        self.gif_label = QLabel()
        self.gif_label.setStyleSheet("border: none;")
        movie = QMovie(GraphicsDirectoryPath('Jarvis.gif'))
        max_gif_size_W = 400
        max_gif_size_H = 225
        movie.setScaledSize(QSize(max_gif_size_W, max_gif_size_H))
        self.gif_label.setAlignment(Qt.AlignRight | Qt.AlignBottom)
        self.gif_label.setMovie(movie)
        movie.start()
        layout.addWidget(self.gif_label)

        self.label = QLabel("")
        self.label.setStyleSheet("""
            color: #00D4FF;
            font-size: 16px;
            font-family: 'Arial';
            margin-right: 150px;
            border: none;
            margin-top: -20px;
        """)
        self.label.setAlignment(Qt.AlignRight)
        layout.addWidget(self.label)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.loadMessages)
        self.timer.timeout.connect(self.SpeechRecogText)
        self.timer.start(5)

        self.setStyleSheet("""
            QScrollBar:vertical {
                border: none;
                background: rgba(20, 20, 30, 0.9);
                width: 8px;
                margin: 0px 0px 0px 0px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #00D4FF;
                min-height: 20px;
                border-radius: 4px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                background: none;
                height: 0px;
            }
            QScrollBar::up-arrow:vertical, QScrollBar::down-arrow:vertical {
                border: none;
                background: none;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
            }
        """)

    def loadMessages(self):
        global old_chat_message
        with open(TempDirectoryPath('Responses.data'), "r", encoding='utf-8') as file:
            messages = file.read()
            if not messages or len(messages) <= 1 or str(old_chat_message) == str(messages):
                return
            self.addMessage(f"{Assistantname}: {messages}", "#00D4FF")
            old_chat_message = messages
        status = GetAssistantStatus()
        if status == "Processing":
            self.typing_label.show()
            self.typing_label.movie().start()
        else:
            self.typing_label.hide()

    def SpeechRecogText(self):
        with open(TempDirectoryPath('Status.data'), "r", encoding='utf-8') as file:
            messages = file.read()
            self.label.setText(messages)

    def addMessage(self, message, color):
        cursor = self.chat_text_edit.textCursor()
        cursor.movePosition(cursor.End)
        format = QTextCharFormat()
        format.setForeground(QColor(color))
        if "**" in message:
            message = message.replace("**", "<b>", 1).replace("**", "</b>", 1)
        cursor.insertHtml(f'<p style="margin: 20px 15px; color: {color}; line-height: 1.5;">{message}</p>')
        cursor.insertHtml('<br>')  # Ensure extra line break for separation
        self.chat_text_edit.setTextCursor(cursor)
        self.chat_text_edit.ensureCursorVisible()

    def send_query(self):
        query = self.input_field.text().strip()
        if query:
            modified_query = QueryModifier(query)
            self.addMessage(f"You: {modified_query}", "#FFFFFF")
            self.input_field.clear()
            with open(TempDirectoryPath('Query.data'), "w", encoding='utf-8') as file:
                file.write(modified_query)

    def upload_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select File", "", "All Files (*);;Images (*.png *.jpg);;PDFs (*.pdf)")
        if file_path:
            self.addMessage(f"Uploaded: {os.path.basename(file_path)}", "#00D4FF")
            with open(TempDirectoryPath('UploadedFile.data'), "w", encoding='utf-8') as file:
                file.write(file_path)

    def show_context_menu(self, pos):
        menu = QMenu()
        copy_action = menu.addAction("Copy")
        delete_action = menu.addAction("Delete")
        action = menu.exec_(self.chat_text_edit.mapToGlobal(pos))
        if action == copy_action:
            self.chat_text_edit.copy()
        elif action == delete_action:
            self.chat_text_edit.clear()

class CircularVisualizer(QWidget):
    """A circular audio visualizer with animated bars, glowing orb, and state-driven colors."""
    NUM_BARS = 64
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

        # 30 FPS animation timer
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick)
        self._anim_timer.start(33)

    # ----- public API -----
    def setState(self, status_text: str):
        """Map the status string from Status.data to an internal state key."""
        t = status_text.lower()
        for key in self.STATE_COLORS:
            if key in t:
                self._state = key
                return
        self._state = 'available'

    # ----- internal animation -----
    def _tick(self):
        self._phase += 0.06
        # Pulse ring
        self._pulse += 0.4 * self._pulse_dir
        if self._pulse > 12 or self._pulse < -4:
            self._pulse_dir *= -1

        # Generate target bar heights based on state
        active = self._state not in ('available',)
        for i in range(self.NUM_BARS):
            if active:
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
    def __init__(self, parent=None):
        super().__init__(parent)
        desktop = QApplication.desktop()
        screen_width = desktop.screenGeometry().width()
        screen_height = desktop.screenGeometry().height()
        scale_factor = min(screen_width / 1920, screen_height / 1080)

        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, int(100 * scale_factor))

        # ---- Circular Audio Visualizer (replaces old GIF) ----
        vis_size = int(380 * scale_factor)
        self.visualizer = CircularVisualizer(size=vis_size)

        # ---- Mic toggle button ----
        self.icon_label = QLabel()
        pixmap = QPixmap(GraphicsDirectoryPath('Mic_on.png'))
        new_pixmap = pixmap.scaled(int(80 * scale_factor), int(80 * scale_factor), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.icon_label.setPixmap(new_pixmap)
        self.icon_label.setFixedSize(int(100 * scale_factor), int(100 * scale_factor))
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setStyleSheet("""
            background-color: rgba(0, 212, 255, 0.2);
            border-radius: 50px;
            padding: 10px;
        """)
        self.toggled = True
        self.toggle_icon()
        self.icon_label.mousePressEvent = self.toggle_icon

        self.label = QLabel("")
        self.label.setStyleSheet("""
            color: #00D4FF;
            font-size: 18px;
            font-family: 'Arial';
            margin-bottom: 20px;
        """)

        content_layout.addStretch(1)
        content_layout.addWidget(self.visualizer, alignment=Qt.AlignCenter)
        content_layout.addWidget(self.label, alignment=Qt.AlignCenter)
        content_layout.addWidget(self.icon_label, alignment=Qt.AlignCenter)
        content_layout.addStretch(1)
        self.setLayout(content_layout)
        self.setFixedHeight(screen_height)
        self.setFixedWidth(screen_width)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.SpeechRecogText)
        self.timer.start(5)

    def paintEvent(self, event):
        painter = QPainter(self)
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0, QColor(20, 20, 30))
        gradient.setColorAt(1, QColor(0, 0, 20))
        painter.fillRect(self.rect(), gradient)
        super().paintEvent(event)

    def SpeechRecogText(self):
        with open(TempDirectoryPath('Status.data'), "r", encoding='utf-8') as file:
            messages = file.read()
            self.label.setText(messages)
            self.visualizer.setState(messages)

    def load_icon(self, path, width, height):
        pixmap = QPixmap(path)
        new_pixmap = pixmap.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.icon_label.setPixmap(new_pixmap)

    def toggle_icon(self, event=None):
        scale_factor = min(QApplication.desktop().screenGeometry().width() / 1920, 
                          QApplication.desktop().screenGeometry().height() / 1080)
        if self.toggled:
            # Mic is ON -> show ON icon and set status to True (listening)
            self.load_icon(GraphicsDirectoryPath('Mic_on.png'), int(80 * scale_factor), int(80 * scale_factor))
            SetMicrophoneStatus("True")
        else:
            # Mic is OFF -> show OFF icon and set status to False (paused)
            self.load_icon(GraphicsDirectoryPath('Mic_off.png'), int(80 * scale_factor), int(80 * scale_factor))
            SetMicrophoneStatus("False")
        self.toggled = not self.toggled

class MessageScreen(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        desktop = QApplication.desktop()
        screen_width = desktop.screenGeometry().width()
        screen_height = desktop.screenGeometry().height()

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        chat_section = ChatSection()
        layout.addWidget(chat_section)
        self.setLayout(layout)
        self.setFixedHeight(screen_height)
        self.setFixedWidth(screen_width)

    def paintEvent(self, event):
        painter = QPainter(self)
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0, QColor(20, 20, 30))
        gradient.setColorAt(1, QColor(0, 0, 20))
        painter.fillRect(self.rect(), gradient)
        super().paintEvent(event)

class CustomTopBar(QWidget):
    def __init__(self, parent, stacked_widget):
        super().__init__(parent)
        self.stacked_widget = stacked_widget
        self.theme = "dark"
        self.initUI()

    def initUI(self):
        self.setFixedHeight(60)
        layout = QHBoxLayout(self)
        layout.setAlignment(Qt.AlignRight)
        layout.setContentsMargins(10, 5, 10, 5)

        title_label = QLabel(f" {str(Assistantname).capitalize()} AI ")
        title_label.setStyleSheet("""
            color: #00D4FF;
            font-size: 20px;
            font-family: 'Arial';
            font-weight: bold;
            background: transparent;
        """)

        home_button = QPushButton(" Home")
        home_icon = QIcon(GraphicsDirectoryPath("Home.png"))
        home_button.setIcon(home_icon)
        home_button.setStyleSheet("""
            QPushButton {
                background-color: #00D4FF;
                color: black;
                border-radius: 15px;
                padding: 8px 16px;
                font-family: 'Arial';
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #00BFFF;
            }
        """)
        home_button.clicked.connect(lambda: self.stacked_widget.setCurrentIndex(0))

        message_button = QPushButton(" Chat")
        message_icon = QIcon(GraphicsDirectoryPath("Chats.png"))
        message_button.setIcon(message_icon)
        message_button.setStyleSheet("""
            QPushButton {
                background-color: #00D4FF;
                color: black;
                border-radius: 15px;
                padding: 8px 16px;
                font-family: 'Arial';
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #00BFFF;
            }
        """)
        message_button.clicked.connect(lambda: self.stacked_widget.setCurrentIndex(1))

        theme_button = QPushButton("Toggle Theme")
        theme_button.setStyleSheet("""
            QPushButton {
                background-color: #00D4FF;
                color: black;
                border-radius: 15px;
                padding: 8px 16px;
                font-family: 'Arial';
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #00BFFF;
            }
        """)
        theme_button.clicked.connect(self.toggle_theme)

        minimize_button = QPushButton()
        minimize_icon = QIcon(GraphicsDirectoryPath('Minimize2.png'))
        minimize_button.setIcon(minimize_icon)
        minimize_button.setStyleSheet("""
            QPushButton {
                background-color: #FF4C4C;
                border-radius: 15px;
                padding: 8px;
            }
            QPushButton:hover {
                background-color: #FF6666;
            }
        """)
        minimize_button.clicked.connect(self.minimizeWindow)

        self.maximize_button = QPushButton()
        self.maximize_icon = QIcon(GraphicsDirectoryPath('Maximize.png'))
        self.restore_icon = QIcon(GraphicsDirectoryPath('Minimize.png'))
        self.maximize_button.setIcon(self.maximize_icon)
        self.maximize_button.setStyleSheet("""
            QPushButton {
                background-color: #FF4C4C;
                border-radius: 15px;
                padding: 8px;
            }
            QPushButton:hover {
                background-color: #FF6666;
            }
        """)
        self.maximize_button.clicked.connect(self.maximizeWindow)

        close_button = QPushButton()
        close_icon = QIcon(GraphicsDirectoryPath('Close.png'))
        close_button.setIcon(close_icon)
        close_button.setStyleSheet("""
            QPushButton {
                background-color: #FF4C4C;
                border-radius: 15px;
                padding: 8px;
            }
            QPushButton:hover {
                background-color: #FF6666;
            }
        """)
        close_button.clicked.connect(self.closeWindow)

        layout.addWidget(title_label)
        layout.addStretch(1)
        layout.addWidget(home_button)
        layout.addWidget(message_button)
        layout.addWidget(theme_button)
        layout.addSpacing(10)
        layout.addWidget(minimize_button)
        layout.addWidget(self.maximize_button)
        layout.addWidget(close_button)

        self.draggable = True
        self.offset = None

    def paintEvent(self, event):
        painter = QPainter(self)
        gradient = QLinearGradient(0, 0, 0, self.height())
        if self.theme == "dark":
            gradient.setColorAt(0, QColor(30, 30, 50))
            gradient.setColorAt(1, QColor(10, 10, 30))
        else:
            gradient.setColorAt(0, QColor(200, 200, 220))
            gradient.setColorAt(1, QColor(180, 180, 200))
        painter.fillRect(self.rect(), gradient)
        super().paintEvent(event)

    def toggle_theme(self):
        if self.theme == "dark":
            self.theme = "light"
            self.parent().setStyleSheet("""
                QWidget { background-color: #FFFFFF; color: #000000; }
                QTextEdit { background-color: #F0F0F0; color: #000000; }
                QLineEdit { background-color: #F0F0F0; color: #000000; }
                QPushButton { background-color: #00BFFF; color: #FFFFFF; }
                QLabel { color: #000000; }
            """)
        else:
            self.theme = "dark"
            self.parent().setStyleSheet("")
        self.update()

    def minimizeWindow(self):
        self.parent().showMinimized()

    def maximizeWindow(self):
        if self.parent().isMaximized():
            self.parent().showNormal()
            self.maximize_button.setIcon(self.maximize_icon)
        else:
            self.parent().showMaximized()
            self.maximize_button.setIcon(self.restore_icon)

    def closeWindow(self):
        self.parent().close()

    def mousePressEvent(self, event):
        if self.draggable:
            self.offset = event.pos()

    def mouseMoveEvent(self, event):
        if self.draggable and self.offset:
            new_pos = event.globalPos() - self.offset
            self.parent().move(new_pos)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.initUI()

    def initUI(self):
        desktop = QApplication.desktop()
        screen_width = desktop.screenGeometry().width()
        screen_height = desktop.screenGeometry().height()

        self.stacked_widget = QStackedWidget(self)
        initial_screen = InitialScreen()
        message_screen = MessageScreen()
        self.stacked_widget.addWidget(initial_screen)
        self.stacked_widget.addWidget(message_screen)

        self.setGeometry(0, 0, screen_width, screen_height)
        top_bar = CustomTopBar(self, self.stacked_widget)
        self.setMenuWidget(top_bar)
        self.setCentralWidget(self.stacked_widget)

        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.statusBar.showMessage("Ready")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_status)
        self.timer.start(1000)

    def update_status(self):
        status = GetAssistantStatus()
        self.statusBar.showMessage(status)

def GraphicalUserInterface():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    GraphicalUserInterface()