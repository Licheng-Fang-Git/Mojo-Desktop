import math
import sys

from PyQt6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QRadialGradient
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bridge import AlertBridge, start_server
from llm import EvaluationBridge, evaluate_async, log_decision

COLLAPSED_SIZE = QSize(50, 50)
PANEL_SIZE = QSize(320, 260)
PANEL_GAP = 12  # space between the orb and the panel, in pixels
PANEL_COLOR = QColor(30, 30, 40, 235)

# A mouse movement smaller than this (in pixels) counts as a click, not a drag.
DRAG_THRESHOLD = 4

# The orb draws inside a slightly smaller radius than the widget itself so
# the idle/thinking "breathing" pulse has room to grow without clipping.
BASE_RADIUS_SCALE = 0.88


def _lerp_color(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
        int(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


def get_orb_gradient_idle(center_x, center_y, radius):
    """Creates the iridescent gold-to-green gradient for the Idle state."""
    gradient = QRadialGradient(QPointF(center_x, center_y), radius)

    gradient.setColorAt(0.0, QColor(180, 140, 100))
    gradient.setColorAt(0.5, QColor(145, 120, 95))
    gradient.setColorAt(0.85, QColor(115, 130, 115))
    gradient.setColorAt(1.0, QColor(115, 130, 115, 0))

    return gradient


def get_orb_gradient_hover(center_x, center_y, radius):
    """Creates a slightly brighter, more vibrant version for the Hover state."""
    gradient = QRadialGradient(QPointF(center_x, center_y), radius)

    gradient.setColorAt(0.0, QColor(210, 165, 120))
    gradient.setColorAt(0.5, QColor(170, 140, 110))
    gradient.setColorAt(0.85, QColor(135, 155, 135))
    gradient.setColorAt(1.0, QColor(135, 155, 135, 0))

    return gradient


def get_orb_gradient_thinking(center_x, center_y, radius, phase):
    """Shimmers between idle and a brighter warm tone while the AI evaluates
    a reason — driven by `phase`, which the caller animates over time."""
    t = (math.sin(phase) + 1) / 2  # oscillates 0..1

    core = _lerp_color(QColor(180, 140, 100), QColor(255, 210, 130), t)
    mid = _lerp_color(QColor(145, 120, 95), QColor(200, 170, 120), t)
    edge = _lerp_color(QColor(115, 130, 115), QColor(170, 190, 140), t)

    gradient = QRadialGradient(QPointF(center_x, center_y), radius)
    gradient.setColorAt(0.0, core)
    gradient.setColorAt(0.5, mid)
    gradient.setColorAt(0.85, edge)
    gradient.setColorAt(1.0, QColor(edge.red(), edge.green(), edge.blue(), 0))
    return gradient


class ChatPanel(QWidget):
    """The intervention bubble that pops up beside the orb. Own top-level
    window so it can float next to the orb instead of replacing it."""

    def __init__(self, orb):
        super().__init__()
        self.orb = orb

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(PANEL_SIZE)

        self.current_site = None
        self.current_tab_id = None
        self._pending_bubble = None
        self.evaluation_bridge = EvaluationBridge()
        self.evaluation_bridge.evaluation_done.connect(self._on_evaluation_done)

        self.message_area = QWidget()
        self.message_area.setStyleSheet("background: transparent;")
        self.message_layout = QVBoxLayout(self.message_area)
        self.message_layout.setContentsMargins(0, 0, 0, 4)
        self.message_layout.setSpacing(8)
        self.message_layout.addStretch()  # keeps bubbles pinned to the bottom

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidget(self.message_area)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setStyleSheet("background: transparent; border: none;")
        self.scroll_area.viewport().setStyleSheet("background: transparent;")

        self.input_line = QLineEdit()
        self.input_line.setPlaceholderText("Type your reason...")
        self.input_line.setStyleSheet(
            "color: white; background: rgba(255,255,255,30); "
            "border: 1px solid rgba(255,255,255,60); border-radius: 6px; padding: 6px;"
        )
        self.input_line.returnPressed.connect(self._on_submit)

        button_style = (
            "QPushButton { color: white; background: rgba(255,255,255,30); "
            "border: 1px solid rgba(255,255,255,60); border-radius: 6px; "
            "padding: 5px 10px; font-size: 11px; }"
            "QPushButton:hover { background: rgba(255,255,255,55); }"
        )
        self.close_button = QPushButton("Close tab")
        self.close_button.setStyleSheet(button_style)
        self.close_button.clicked.connect(self._close_tab)

        self.docs_button = QPushButton("Open Google Docs")
        self.docs_button.setStyleSheet(button_style)
        self.docs_button.clicked.connect(
            lambda: self._open_url("https://docs.google.com", "Google Docs")
        )

        button_row = QHBoxLayout()
        button_row.addWidget(self.close_button)
        button_row.addWidget(self.docs_button)
        button_row.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.addWidget(self.scroll_area)
        layout.addLayout(button_row)
        layout.addWidget(self.input_line)

    def _add_bubble(self, text, sender):
        label = QLabel(text)
        label.setWordWrap(True)
        label.setMaximumWidth(220)

        if sender == "ai":
            label.setStyleSheet(
                "background: rgba(255,255,255,28); color: white; "
                "border-radius: 10px; padding: 8px 10px; font-size: 12px;"
            )
        else:
            label.setStyleSheet(
                "background: rgba(80,160,255,190); color: white; "
                "border-radius: 10px; padding: 8px 10px; font-size: 12px;"
            )

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        if sender == "ai":
            row.addWidget(label)
            row.addStretch()
        else:
            row.addStretch()
            row.addWidget(label)

        # Insert before the trailing stretch so new bubbles land at the bottom.
        self.message_layout.insertLayout(self.message_layout.count() - 1, row)
        QTimer.singleShot(0, self._scroll_to_bottom)
        return label

    def _scroll_to_bottom(self):
        bar = self.scroll_area.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _clear_conversation(self):
        while self.message_layout.count() > 1:
            item = self.message_layout.takeAt(0)
            row = item.layout()
            if row is not None:
                while row.count():
                    child = row.takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()
                row.deleteLater()

    def show_alert(self, site, message, tab_id):
        self.current_site = site
        self.current_tab_id = tab_id
        self._pending_bubble = None
        self._clear_conversation()
        self._add_bubble(message or f"Distraction detected: {site}", sender="ai")

    def _on_submit(self):
        reason = self.input_line.text().strip()
        if not reason or not self.current_site:
            return

        self.input_line.clear()
        self.input_line.setEnabled(False)
        self._add_bubble(reason, sender="user")
        self._pending_bubble = self._add_bubble("Thinking...", sender="ai")
        self.orb.set_thinking(True)
        evaluate_async(self.current_site, reason, self.evaluation_bridge)

    def _on_evaluation_done(self, site, reason, decision):
        self.input_line.setEnabled(True)
        self.orb.set_thinking(False)
        if self._pending_bubble is not None:
            self._pending_bubble.setText(decision.get("response", "..."))
            self._pending_bubble = None
        log_decision(site, reason, decision)

        # Only "deny" closes the tab — "allow" and "maybe" leave it open.
        # "none" still resolves the extension's poll so it stops checking,
        # it just tells it not to touch the tab.
        if self.current_tab_id is not None:
            verdict = decision.get("decision")
            action = {"type": "close"} if verdict == "deny" else {"type": "none"}
            self.orb.bridge.record_action(self.current_tab_id, action)

    def _close_tab(self):
        if self.current_tab_id is None:
            self._add_bubble("No active tab to close.", sender="ai")
            return
        self._add_bubble("Closing the tab.", sender="ai")
        self.orb.bridge.record_action(self.current_tab_id, {"type": "close"})

    def _open_url(self, url, label):
        if self.current_tab_id is None:
            self._add_bubble("No active tab to redirect.", sender="ai")
            return
        self._add_bubble(f"Opening {label} instead.", sender="ai")
        self.orb.bridge.record_action(self.current_tab_id, {"type": "open", "url": url})

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 16, 16)
        painter.fillPath(path, PANEL_COLOR)


class Orb(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)

        self._hovering = False
        self._thinking = False
        self._dragging = False
        self._dragged = False  # did the mouse move past the threshold?
        self._drag_offset = QPoint()
        self._pulse_phase = 0.0
        self.width = COLLAPSED_SIZE.width()
        self.height = COLLAPSED_SIZE.height()

        self.chat_panel = ChatPanel(self)

        self.resize(COLLAPSED_SIZE)
        self.move(1800, 100)

        self.bridge = AlertBridge()
        self.bridge.alert_received.connect(self._on_alert)
        self.server = start_server(self.bridge)

        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._advance_pulse)
        self._pulse_timer.start(33)  # ~30 FPS

    def set_thinking(self, thinking: bool):
        self._thinking = thinking
        self.update()

    def _advance_pulse(self):
        step = 0.22 if self._thinking else 0.05
        self._pulse_phase = (self._pulse_phase + step) % (2 * math.pi)
        self.update()

    def _on_alert(self, site, message, tab_id):
        self.chat_panel.show_alert(site, message, tab_id)
        self._position_chat_panel()
        self.chat_panel.show()

    def closeEvent(self, event):
        self.server.shutdown()
        super().closeEvent(event)

    def _toggle_chat(self):
        if self.chat_panel.isVisible():
            self.chat_panel.hide()
        else:
            self._position_chat_panel()
            self.chat_panel.show()

    def _position_chat_panel(self):
        orb_geo = self.geometry()
        screen_geo = self.screen().availableGeometry()
        panel_w = self.chat_panel.width()
        panel_h = self.chat_panel.height()

        # Prefer opening to the right of the orb; flip to the left if there's
        # no room (e.g. orb is dragged near the right edge of the screen).
        x = orb_geo.right() + PANEL_GAP
        if x + panel_w > screen_geo.right():
            x = orb_geo.left() - PANEL_GAP - panel_w

        y = orb_geo.top()
        if y + panel_h > screen_geo.bottom():
            y = screen_geo.bottom() - panel_h

        self.chat_panel.move(x, y)

    # --- Mouse interaction: drag to move, click (no drag) to toggle chat ---

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._dragged = False
            self._drag_offset = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if self._dragging:
            moved = event.position().toPoint() - self._drag_offset
            if moved.manhattanLength() > DRAG_THRESHOLD:
                self._dragged = True
            self.move(self.pos() + moved)
            if self.chat_panel.isVisible():
                self._position_chat_panel()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            if not self._dragged:
                self._toggle_chat()
            self._dragging = False

    def enterEvent(self, event):
        self._hovering = True
        self.update()

    def leaveEvent(self, event):
        self._hovering = False
        self.update()

    # --- Painting ---

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        cx, cy = self.width / 2, self.height / 2
        base_radius = min(self.width, self.height) / 2 * BASE_RADIUS_SCALE

        # Breathing pulse: gentle while idle/hovering, faster and larger
        # amplitude while the AI is evaluating a reason.
        pulse = 0.5 + 0.5 * math.sin(self._pulse_phase)
        amplitude = 0.14 if self._thinking else 0.05
        radius = base_radius * (1 - amplitude / 2 + amplitude * pulse)

        if self._thinking:
            gradient = get_orb_gradient_thinking(cx, cy, radius, self._pulse_phase)
        elif self._hovering:
            gradient = get_orb_gradient_hover(cx, cy, radius)
        else:
            gradient = get_orb_gradient_idle(cx, cy, radius)

        painter.setBrush(gradient)
        painter.setPen(Qt.PenStyle.NoPen)  # No harsh borders

        painter.drawEllipse(QPointF(cx, cy), radius, radius)


def main():
    app = QApplication(sys.argv)
    orb = Orb()
    orb.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
