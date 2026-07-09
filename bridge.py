import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PyQt6.QtCore import QObject, pyqtSignal

HOST = "localhost"
PORT = 8000


class AlertBridge(QObject):
    """Lives on the GUI thread. The server thread emits this signal instead
    of touching any widgets directly — Qt marshals the emit across threads
    and delivers it to slots safely on the GUI thread."""

    alert_received = pyqtSignal(str, str)  # site, message


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
            bridge.alert_received.emit(site, message)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')

        def log_message(self, format, *args):
            pass  # silence default per-request console logging

    return AlertHandler


def start_server(bridge: AlertBridge) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((HOST, PORT), _make_handler(bridge))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
