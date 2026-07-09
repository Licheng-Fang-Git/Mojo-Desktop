import sys

from PyQt6.QtCore import QPoint, QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QRadialGradient
from PyQt6.QtWidgets import QApplication, QLabel, QLineEdit, QVBoxLayout, QWidget

from bridge import AlertBridge, start_server

COLLAPSED_SIZE = QSize(50, 50)
PANEL_SIZE = QSize(320, 220)
PANEL_GAP = 12  # space between the orb and the panel, in pixels
PANEL_COLOR = QColor(30, 30, 40, 235)

# A mouse movement smaller than this (in pixels) counts as a click, not a drag.
DRAG_THRESHOLD = 4


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


class ChatPanel(QWidget):
    """The intervention bubble that pops up beside the orb. Own top-level
    window so it can float next to the orb instead of replacing it."""

    def __init__(self):
        super().__init__()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(PANEL_SIZE)

        # Placeholder chat view for now — no LLM wired up until Milestone 4.
        self.message_label = QLabel("What are you doing here?")
        self.message_label.setWordWrap(True)
        self.message_label.setStyleSheet("color: white; background: transparent; font-size: 13px;")

        self.input_line = QLineEdit()
        self.input_line.setPlaceholderText("Type your reason...")
        self.input_line.setStyleSheet(
            "color: white; background: rgba(255,255,255,30); "
            "border: 1px solid rgba(255,255,255,60); border-radius: 6px; padding: 6px;"
        )
        self.input_line.returnPressed.connect(self._on_submit)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.addWidget(self.message_label)
        layout.addStretch()
        layout.addWidget(self.input_line)

    def _on_submit(self):
        # Stub for now — Milestone 4 will send this to the LLM for evaluation.
        self.input_line.clear()

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
        self._dragging = False
        self._dragged = False  # did the mouse move past the threshold?
        self._drag_offset = QPoint()
        self.width = COLLAPSED_SIZE.width()
        self.height = COLLAPSED_SIZE.height()

        self.chat_panel = ChatPanel()

        self.resize(COLLAPSED_SIZE)
        self.move(1800, 100)

        self.bridge = AlertBridge()
        self.bridge.alert_received.connect(self._on_alert)
        self.server = start_server(self.bridge)

    def _on_alert(self, site, message):
        self.chat_panel.message_label.setText(message or f"Distraction detected: {site}")
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

        # Define your orb's geometry
        cx, cy = self.width / 2, self.height / 2
        radius = min(self.width, self.height) / 2

        # Choose gradient based on your current state tracking variable
        if self._hovering:
            gradient = get_orb_gradient_hover(cx, cy, radius)
        else:
            gradient = get_orb_gradient_idle(cx, cy, radius)

        # Apply the gradient
        painter.setBrush(gradient)
        painter.setPen(Qt.PenStyle.NoPen)  # No harsh borders

        # Draw the orb
        painter.drawEllipse(QPointF(cx, cy), radius, radius)


def main():
    app = QApplication(sys.argv)
    orb = Orb()
    orb.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
