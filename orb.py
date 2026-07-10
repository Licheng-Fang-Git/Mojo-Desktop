import math
import sys

from PyQt6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QRadialGradient
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from bridge import AlertBridge, start_server
from llm import EvaluationBridge, evaluate_async, log_decision

COLLAPSED_SIZE = QSize(50, 50)
PANEL_SIZE = QSize(320, 220)
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
    """Cool blue/cyan tones while the AI evaluates — deliberately a
    different hue family from idle/hover's warm gold-green, not just a
    brighter version of the same color, so the state change is obvious even
    before you notice any motion."""
    t = (math.sin(phase) + 1) / 2  # oscillates 0..1

    core = _lerp_color(QColor(60, 140, 220), QColor(150, 215, 255), t)
    mid = _lerp_color(QColor(40, 100, 180), QColor(90, 170, 230), t)
    edge = _lerp_color(QColor(30, 70, 140), QColor(70, 140, 200), t)

    gradient = QRadialGradient(QPointF(center_x, center_y), radius)
    gradient.setColorAt(0.0, core)
    gradient.setColorAt(0.5, mid)
    gradient.setColorAt(0.85, edge)
    gradient.setColorAt(1.0, QColor(edge.red(), edge.green(), edge.blue(), 0))
    return gradient


class ChatPanel(QWidget):
    """The intervention bubble that pops up beside the orb. Own top-level
    window so it can float next to the orb instead of replacing it.

    Only ever shows the latest exchange — the AI's current message and your
    most recent reason — not a scrollable history."""

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
        self.evaluation_bridge = EvaluationBridge()
        self.evaluation_bridge.evaluation_done.connect(self._on_evaluation_done)

        self.countdown_label = QLabel("")
        self.countdown_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.countdown_label.setStyleSheet(
            "color: rgb(120, 230, 160); background: rgba(90, 220, 140, 35); "
            "border: 1px solid rgba(90, 220, 140, 120); border-radius: 6px; "
            "padding: 4px; font-size: 12px; font-weight: bold;"
        )
        self.countdown_label.hide()  # only shown while an allowance timer is running

        self.message_label = QLabel("Open Tabs to see the AI's message here.")
        self.message_label.setWordWrap(True)
        self.message_label.setStyleSheet("color: white; background: transparent; font-size: 13px;")

        self.reason_label = QLabel("")
        self.reason_label.setWordWrap(True)
        self.reason_label.setStyleSheet(
            "color: rgba(255,255,255,150); background: transparent; font-size: 11px; font-style: italic;"
        )

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
        layout.addWidget(self.countdown_label)
        layout.addWidget(self.message_label)
        layout.addWidget(self.reason_label)
        layout.addStretch()
        layout.addLayout(button_row)
        layout.addWidget(self.input_line)

    def show_alert(self, site, message, tab_id):
        self.current_site = site
        self.current_tab_id = tab_id
        self.message_label.setText(message or f"Distraction detected: {site}")
        self.reason_label.setText("")
        self.refresh_countdown()

    def refresh_countdown(self):
        seconds = self.orb.countdown_seconds_for(self.current_tab_id)
        if seconds is None:
            self.countdown_label.hide()
        else:
            minutes, secs = divmod(max(0, seconds), 60)
            self.countdown_label.setText(f"⏱ {minutes}:{secs:02d} remaining")
            self.countdown_label.show()

    def _on_submit(self):
        reason = self.input_line.text().strip()
        if not reason or not self.current_site:
            return

        self.input_line.clear()
        self.input_line.setEnabled(False)
        self.reason_label.setText(f"You said: \"{reason}\"")
        self.message_label.setText("Thinking...")
        self.orb.set_thinking(True)
        evaluate_async(self.current_site, reason, self.evaluation_bridge)

    def _on_evaluation_done(self, site, reason, decision):
        self.input_line.setEnabled(True)
        self.orb.set_thinking(False)
        self.message_label.setText(decision.get("response", "..."))
        log_decision(site, reason, decision)

        # No auto-hide here — the verdict (often a long roast) needs to stay
        # readable, and you may still want to click Close tab / Open Docs.
        if self.current_tab_id is None:
            return

        if decision.get("decision") == "deny":
            self.orb.bridge.record_action(self.current_tab_id, {"type": "close"})
            self.orb.cancel_countdown(self.current_tab_id)
        else:
            # Deliberately NOT recording a "none" action here. The extension
            # treats any non-null action as final and stops polling that
            # tab — but an allow isn't final, it's a ticking allowance. The
            # extension just keeps quietly polling (its alarm is already
            # running from the original alert) until the countdown below
            # eventually records a forced "close".
            duration = decision.get("duration")
            try:
                minutes = max(1, int(duration))
            except (TypeError, ValueError):
                minutes = 10  # safe default if the model omitted/mangled it
            self.orb.start_countdown(self.current_tab_id, minutes)
        self.refresh_countdown()

    def _close_tab(self):
        if self.current_tab_id is None:
            self.message_label.setText("No active tab to close.")
            return
        self.message_label.setText("Closing the tab.")
        self.orb.bridge.record_action(self.current_tab_id, {"type": "close"})
        self.orb.cancel_countdown(self.current_tab_id)
        self.orb.flash(QColor(230, 70, 70))
        QTimer.singleShot(1000, self.hide)

    def _open_url(self, url, label):
        if self.current_tab_id is None:
            self.message_label.setText("No active tab to redirect.")
            return
        self.message_label.setText(f"Opening {label} instead.")
        self.orb.bridge.record_action(self.current_tab_id, {"type": "open", "url": url})
        self.orb.cancel_countdown(self.current_tab_id)
        self.orb.flash(QColor(70, 200, 130))
        QTimer.singleShot(1000, self.hide)

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
            | Qt.WindowType.Window
        )

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)

        self._hovering = False
        self._thinking = False
        self._flash_color = None
        self._countdowns = {}  # tab_id -> {"remaining": seconds, "total": seconds}
        self._dragging = False
        self._dragged = False  # did the mouse move past the threshold?
        self._drag_offset = QPoint()
        self._pulse_phase = 0.0
        self.width = COLLAPSED_SIZE.width()
        self.height = COLLAPSED_SIZE.height()

        self.chat_panel = ChatPanel(self)

        self.resize(COLLAPSED_SIZE)
        self.move(100, 100)

        self.bridge = AlertBridge()
        self.bridge.alert_received.connect(self._on_alert)
        self.server = start_server(self.bridge)

        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._advance_pulse)
        self._pulse_timer.start(33)  # ~30 FPS

        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._end_flash)

        self._countdown_timer = QTimer(self)
        self._countdown_timer.timeout.connect(self._tick_countdowns)
        self._countdown_timer.start(1000)

    def set_thinking(self, thinking: bool):
        self._thinking = thinking
        self.update()

    def start_countdown(self, tab_id, minutes):
        """Runs independently of whether the chat panel is open or even
        showing this tab — enforcement happens here regardless of what's
        currently visible."""
        total_seconds = max(1, int(minutes)) * 60
        self._countdowns[tab_id] = {"remaining": total_seconds, "total": total_seconds}
        self.update()

    def cancel_countdown(self, tab_id):
        self._countdowns.pop(tab_id, None)

    def countdown_seconds_for(self, tab_id):
        entry = self._countdowns.get(tab_id)
        return entry["remaining"] if entry else None

    def _tick_countdowns(self):
        if not self._countdowns:
            return

        expired = []
        for tab_id, entry in self._countdowns.items():
            entry["remaining"] -= 1
            if entry["remaining"] <= 0:
                expired.append(tab_id)

        for tab_id in expired:
            del self._countdowns[tab_id]
            self.bridge.record_action(tab_id, {"type": "close"})

        self.chat_panel.refresh_countdown()
        self.update()

    def flash(self, color: QColor, duration_ms: int = 500):
        """Brief solid-color flash for a one-shot action (closing/opening a
        tab) — distinct from the ongoing 'thinking' state, since these
        actions resolve instantly rather than taking evaluation time."""
        self._flash_color = color
        self.update()
        self._flash_timer.start(duration_ms)

    def _end_flash(self):
        self._flash_color = None
        self.update()

    def _advance_pulse(self):
        step = 0.22 if self._thinking else 0.05
        self._pulse_phase = (self._pulse_phase + step) % (2 * math.pi)
        self.update()

    def _on_alert(self, site, message, tab_id):
        self.chat_panel.show_alert(site, message, tab_id)
        self._position_chat_panel()
        self.chat_panel.show()
        self.chat_panel.raise_()
        self.chat_panel.activateWindow()

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

        # Breathing pulse: barely-there while idle/hovering, large and fast
        # while the AI is evaluating a reason — needs to be big enough to
        # actually register at 50x50 pixels.
        pulse = 0.5 + 0.5 * math.sin(self._pulse_phase)
        amplitude = 0.35 if self._thinking else 0.05
        radius = base_radius * (1 - amplitude / 2 + amplitude * pulse)

        if self._flash_color is not None:
            gradient = QRadialGradient(QPointF(cx, cy), radius)
            gradient.setColorAt(0.0, self._flash_color)
            faded = QColor(self._flash_color)
            faded.setAlpha(0)
            gradient.setColorAt(1.0, faded)
        elif self._thinking:
            gradient = get_orb_gradient_thinking(cx, cy, radius, self._pulse_phase)
        elif self._hovering:
            gradient = get_orb_gradient_hover(cx, cy, radius)
        else:
            gradient = get_orb_gradient_idle(cx, cy, radius)

        painter.setBrush(gradient)
        painter.drawEllipse(QPointF(cx, cy), radius, radius)

        if self._thinking:
            # A rotating spinner ring reads as "actively working" far more
            # clearly than a small internal glint did — motion around the
            # whole edge is much easier to notice at this size than a
            # subtle shift inside the fill.
            pen = QPen(QColor(140, 220, 255, 235), 3)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            ring_radius = base_radius * 1.05
            rect = QRectF(cx - ring_radius, cy - ring_radius, ring_radius * 2, ring_radius * 2)
            start_angle = int(math.degrees(self._pulse_phase * 2.5) * 16) % (360 * 16)
            painter.drawArc(rect, start_angle, 100 * 16)
        else:
            # Shrinking countdown ring for whichever tab the chat panel is
            # currently showing — a static pie-timer sweep, not animated
            # motion like the thinking spinner, since it represents time
            # elapsing rather than active processing.
            countdown = self._countdowns.get(self.chat_panel.current_tab_id)
            if countdown:
                fraction = countdown["remaining"] / countdown["total"]
                pen = QPen(QColor(90, 220, 140, 230), 3)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                ring_radius = base_radius * 1.05
                rect = QRectF(cx - ring_radius, cy - ring_radius, ring_radius * 2, ring_radius * 2)
                # 0 degrees = 3 o'clock in Qt's angle system; +90*16 starts
                # at 12 o'clock, and a negative span sweeps clockwise as
                # time runs out.
                painter.drawArc(rect, 90 * 16, -int(360 * fraction * 16))


def main():
    app = QApplication(sys.argv)
    orb = Orb()
    orb.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
