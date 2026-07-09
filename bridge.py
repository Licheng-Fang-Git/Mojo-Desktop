import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from PyQt6.QtCore import QObject, pyqtSignal

HOST = "localhost"
PORT = 8000


class AlertBridge(QObject):
    """Lives on the GUI thread. The server thread emits this signal instead
    of touching any widgets directly — Qt marshals the emit across threads
    and delivers it to slots safely on the GUI thread.

    Also holds the pending-action store: once the GUI decides what should
    happen to a tab, it's recorded here as an action dict so the extension's
    poll (GET /decision) can pick it up and act on it — either
    {"type": "close"} or {"type": "open", "url": "..."}."""

    alert_received = pyqtSignal(str, str, int)  # site, message, tab_id

    def __init__(self):
        super().__init__()
        self._actions = {}
        self._lock = threading.Lock()

    def record_action(self, tab_id: int, action: dict):
        with self._lock:
            self._actions[tab_id] = action

    def pop_action(self, tab_id: int):
        with self._lock:
            return self._actions.pop(tab_id, None)


def _make_handler(bridge: AlertBridge):
    class AlertHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/alert":
                self.send_response(404)
                self.end_headers()
                return

            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body or b"{}")
            except json.JSONDecodeError:
                data = {}

            site = data.get("site", "")
            message = data.get("message", "Closing distraction...")
            tab_id = data.get("tab_id", -1)
            bridge.alert_received.emit(site, message, tab_id)

            self._send_json({"status": "ok"})

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path != "/decision":
                self.send_response(404)
                self.end_headers()
                return

            query = parse_qs(parsed.query)
            try:
                tab_id = int(query.get("tab_id", [-1])[0])
            except ValueError:
                tab_id = -1

            action = bridge.pop_action(tab_id)
            self._send_json({"action": action})

        def _send_json(self, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass  # silence default per-request console logging

    return AlertHandler


def start_server(bridge: AlertBridge) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((HOST, PORT), _make_handler(bridge))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
