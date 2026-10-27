"""Isolated acceptance receiver and synthetic metrics; never a production adapter."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

lock = threading.Lock()
database_up = True
deliveries = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, status, data, content_type="application/json"):
        body = data.encode() if isinstance(data, str) else json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        with lock:
            if self.path == "/metrics":
                self.reply(200, f"platform_database_up {int(database_up)}\n", "text/plain")
            elif self.path == "/evidence":
                self.reply(200, {"deliveries": list(deliveries)})
            else:
                self.reply(404, {})

    def do_POST(self):
        global database_up
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        with lock:
            if self.path == "/control":
                database_up = bool(body["database_up"])
            elif self.path == "/alerts":
                # Store only routing evidence; no URLs, user content, or configuration.
                deliveries.append({"status": body["status"], "alerts": [
                    {"status": alert["status"], "alertname": alert["labels"].get("alertname")}
                    for alert in body.get("alerts", [])
                ]})
            else:
                self.reply(404, {})
                return
        self.reply(200, {})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
