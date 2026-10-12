"""A loopback-only dashboard, with background refresh and GET-only FPL import."""

import json
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .http import DataError
from .pipeline import public_team, run, selection, state
from .analytics import player_analytics
from .rivals import rivals_state, import_rival, edit_rival, refresh_rivals


class Job:
    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.Lock()
        self.current = {"running": False, "messages": [], "error": None, "finished_at": None}

    def status(self):
        with self.lock:
            return dict(self.current)

    def start(self):
        with self.lock:
            if self.current["running"]:
                return False
            self.current = {"running": True, "messages": [], "error": None, "finished_at": None}

        def progress(message):
            with self.lock:
                self.current["messages"] = (self.current["messages"] + [message])[-30:]
            print(message, flush=True)

        def work():
            try:
                run(self.root, progress=progress)
                result = refresh_rivals(self.root, progress=progress)
                if result["errors"]:
                    progress(f"Player refresh complete; {len(result['errors'])} rival snapshots could not refresh. See Rivals.")
            except Exception as exc:
                with self.lock:
                    self.current["error"] = str(exc)
                print(f"Refresh failed: {exc}", flush=True)
            finally:
                with self.lock:
                    self.current["running"] = False
                    self.current["finished_at"] = datetime.now(timezone.utc).isoformat()

        threading.Thread(target=work, daemon=True).start()
        return True


def make_handler(root, job, port):
    root = Path(root)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            # Do not turn public entry IDs into persistent access logs.
            return

        def send(self, code, value, kind="application/json; charset=utf-8"):
            if isinstance(value, (dict, list)):
                value = json.dumps(value, allow_nan=False).encode()
            elif isinstance(value, str):
                value = value.encode()
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(value)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(value)

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            try:
                if path == "/":
                    self.send(200, (root / "fpl_advisor/static/index.html").read_bytes(), "text/html; charset=utf-8")
                elif path == "/api/state":
                    self.send(200, state(root))
                elif path == "/api/status":
                    self.send(200, job.status())
                elif path in ("/app.js", "/charts.js", "/dashboard_features.js", "/style.css"):
                    kind = "text/javascript; charset=utf-8" if path.endswith(".js") else "text/css; charset=utf-8"
                    self.send(200, (root / "fpl_advisor/static" / path[1:]).read_bytes(), kind)
                elif path == "/api/rivals":
                    self.send(200, rivals_state(root))
                elif path == "/api/analytics":
                    self.send(200, player_analytics(root))
                elif path == "/forecasts.csv":
                    self.send(200, (root / "reports/player_forecasts.csv").read_bytes(), "text/csv; charset=utf-8")
                else:
                    self.send(404, {"error": "Not found"})
            except Exception as exc:
                self.send(500, {"error": str(exc)})

        def do_POST(self):
            origin = self.headers.get("Origin")
            if origin and origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
                return self.send(403, {"error": "Only this local dashboard may submit requests."})
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self.send(415, {"error": "JSON requests are required."})
            try:
                size = int(self.headers.get("Content-Length", 0))
                if size < 1 or size > 200_000:
                    return self.send(413, {"error": "Invalid request size."})
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise DataError("Expected a JSON object.")
                if self.path == "/api/refresh":
                    self.send(202, {"started": job.start()})
                elif self.path == "/api/import":
                    self.send(200, public_team(root, payload.get("entry_id", "")))
                elif self.path == "/api/select":
                    self.send(200, selection(root, payload))
                elif self.path == "/api/rivals/import":
                    self.send(200, import_rival(root, payload.get("entry_id", ""),
                                                own_entry_id=payload.get("own_entry_id")))
                elif self.path == "/api/rivals/edit":
                    self.send(200, edit_rival(root, payload.get("entry_id", ""),
                                              remove=payload.get("remove") is True, enabled=payload.get("enabled")))
                elif self.path == "/api/rivals/refresh":
                    self.send(200, refresh_rivals(root))
                else:
                    self.send(404, {"error": "Not found"})
            except (DataError, ValueError, TypeError, KeyError) as exc:
                self.send(400, {"error": str(exc)})
            except Exception as exc:
                self.send(500, {"error": str(exc)})

    return Handler


def serve(root, port=8765, open_browser=False, auto_hours=None):
    job = Job(root)
    server = ThreadingHTTPServer(("127.0.0.1", int(port)), make_handler(root, job, int(port)))
    server.daemon_threads = True
    url = f"http://127.0.0.1:{port}"
    print(f"FPL Advisor is ready at {url}. Keep this terminal open. Ctrl+C stops it.", flush=True)
    if auto_hours:
        if float(auto_hours) < 1:
            raise DataError("Automatic refresh interval must be at least one hour.")

        def scheduler():
            job.start()
            while True:
                time.sleep(float(auto_hours) * 3600)
                job.start()

        threading.Thread(target=scheduler, daemon=True).start()
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Dashboard stopped.")
    finally:
        server.server_close()
