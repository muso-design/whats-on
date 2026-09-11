"""Plinth on this PC: the hub, served locally, with a refresh button that works.

The published copy on GitHub Pages cannot start anything on your computer, so
a refresh button there could only ever be decoration. This small server is
what makes the button real. It serves the page this PC has just built and a
tiny API beside it:

  GET  /api/ping      is Plinth's server here? (the page shows the button if so)
  POST /api/refresh   start a refresh, unless one is already running
  GET  /api/status    how far it has got: a step, a percentage, the new counts

The refresh itself is scheduled_refresh.py --manual, run as a child process -
the same path as the schedule and the console, so the button is exactly as
tested as they are. Its output is read line by line and turned into a step
name and a percentage the page can draw as a bar.

It listens on 127.0.0.1 only, answers only requests addressed to that exact
host (a guard against DNS rebinding) and refuses a POST from any other origin,
so no website you visit can press the button for you. It serves a short list
of files by name, never a path. With nothing asked of it for half an hour and
no refresh running, it shuts itself down; plinth.pyw starts it again.

Run: python plinth_server.py   (plinth.pyw does this for you)
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PLINTH_PORT", "47817"))
IDLE_MINUTES = 30

# Everything the page needs, by name. Nothing else in the folder is served.
FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
    "/icon-maskable-512.png": ("icon-maskable-512.png", "image/png"),
    "/icon-180.png": ("icon-180.png", "image/png"),
    "/icon.ico": ("icon.ico", "image/x-icon"),
}

# What the refresh prints, in the order it prints it, as progress. The label
# is what the page shows; the number only ever moves forward.
MILESTONES = [
    ("Getting what GitHub has", 1, "Getting the latest from GitHub"),
    ("Checking the graphics card", 2, "Checking the graphics card"),
    ("Graphics card free", 3, "Checking the graphics card"),
    ("graphics card is busy", 3, "Checking the graphics card"),
    ("Ollama is not running", 3, "Checking the graphics card"),
    ("fetching...", 6, "Reading Leipzig, Halle, Dresden and Chemnitz"),
    ("rundgang/chemnitz", 14, "Reading Berlin"),
    ("art-at-berlin", 20, "Reading Berlin"),
    ("index-berlin", 24, "Reading Berlin"),
    ("berlin-art-link", 27, "Merging what the listings found"),
    ("direct: reading", 30, "Reading gallery sites with the local model"),
    ("direct:", 46, "Placing venues on the map"),
    ("coordinates:", 50, "Translating German descriptions"),
    ("english:", 60, "Recognising artists"),
    ("artists:", 64, "Scoring the shows"),
    ("inventory:", 68, "Open calls: BBK"),
    ("bbk:", 72, "Open calls: ArtConnect"),
    ("artconnect:", 80, "Open calls: opencallforartists"),
    ("opencallforartists:", 86, "Checking who may apply"),
    ("eligibility:", 91, "Merging and ranking the calls"),
    ("wrote ", 95, "Publishing for your phone"),
    ("Published.", 100, "Done"),
    ("Nothing changed", 100, "Done"),
]

_NEW = re.compile(r"(\d+) new shows?, (\d+) new open calls?")

# The two long steps count their items, and the bar moves with each one
# between the percentages either side. Before this, the first translation
# backlog sat on one label for seven minutes and looked stuck.
COUNTERS = [
    (re.compile(r"translating (\d+) of (\d+)"), 50, 60,
     "Translating German descriptions"),
    (re.compile(r"reading terms (\d+) of (\d+)"), 86, 91,
     "Checking who may apply"),
]


def refresh_command():
    """The child process a refresh runs; replaced in the tests."""
    return [sys.executable, os.path.join(HERE, "scheduled_refresh.py"),
            "--manual", "--no-open", "--no-wait"]


class Job:
    """One refresh at a time, and what it has said so far."""

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.state = "idle"            # idle | running | done | failed | busy
        self.lines = []
        self.percent = 0
        self.step = ""
        self.shows = self.calls = None
        self.message = ""
        self.started = self.finished = None

    def snapshot(self, since=0):
        with self.lock:
            return {
                "state": self.state, "percent": self.percent, "step": self.step,
                "lines": self.lines[since:], "next": len(self.lines),
                "new_shows": self.shows, "new_calls": self.calls,
                "message": self.message, "started": self.started,
                "finished": self.finished,
            }

    def start(self, command=None):
        with self.lock:
            if self.state == "running":
                return False
            self.reset()
            self.state = "running"
            self.step = "Starting"
            self.started = datetime.now().isoformat(timespec="seconds")
        threading.Thread(target=self._run, args=(command or refresh_command(),),
                         daemon=True).start()
        return True

    def feed(self, line):
        """Read one line of the refresh's output into progress."""
        line = line.rstrip()
        if not line.strip() or "Press Enter" in line:
            return
        with self.lock:
            self.lines.append(line.strip())
            for needle, percent, label in MILESTONES:
                if needle in line and percent >= self.percent:
                    self.percent, self.step = percent, label
            for pattern, low, high, label in COUNTERS:
                counted = pattern.search(line)
                if counted:
                    done, total = int(counted.group(1)), max(1, int(counted.group(2)))
                    percent = low + (high - low) * min(done, total) // total
                    if percent >= self.percent:
                        self.percent = percent
                        self.step = "%s: %d of %d" % (label, done, total)
            found = _NEW.search(line)
            if found:
                self.shows, self.calls = int(found.group(1)), int(found.group(2))
            # The refresh's own words, exactly: index-berlin reports
            # "251 exhibitions (188 already running)" - shows that are on -
            # and a loose match took that for a scheduled refresh in progress.
            if line.strip().startswith("A refresh is already running"):
                self.state, self.message = "busy", line.strip()
            elif re.search(r"failed|Could not|Stopped", line):
                self.message = line.strip()

    def _run(self, command):
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                 | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
        try:
            process = subprocess.Popen(
                command, cwd=HERE, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", creationflags=flags)
            for line in process.stdout:
                self.feed(line)
            code = process.wait()
        except Exception as exc:                               # noqa: BLE001
            code, self.message = 1, "Could not start the refresh: %s" % exc
        with self.lock:
            self.finished = datetime.now().isoformat(timespec="seconds")
            if self.state == "busy":
                return
            if code == 0 and self.percent >= 100:
                self.state, self.step = "done", "Done"
            else:
                self.state = "failed"
                self.message = self.message or "The refresh stopped early; see refresh.log."


JOB = Job()
LAST_SEEN = [time.time()]


class Handler(BaseHTTPRequestHandler):
    server_version = "Plinth"

    def log_message(self, *args):
        pass                          # quiet; the refresh keeps its own log

    # -- guards -------------------------------------------------------------
    def _allowed_host(self):
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in ("127.0.0.1:%d" % port,
                                                "localhost:%d" % port)

    def _allowed_origin(self):
        origin = self.headers.get("Origin")
        if origin is None:
            return True               # same-origin fetches may omit it
        port = self.server.server_address[1]
        return origin in ("http://127.0.0.1:%d" % port, "http://localhost:%d" % port)

    # -- responses ----------------------------------------------------------
    def _send(self, code, body, kind, extra=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, payload, code=200):
        self._send(code, json.dumps(payload), "application/json")

    def do_GET(self):
        LAST_SEEN[0] = time.time()
        if not self._allowed_host():
            return self._send(403, "wrong host", "text/plain")
        url = urlparse(self.path)
        if url.path == "/api/ping":
            return self._json({"app": "plinth", "state": JOB.state})
        if url.path == "/api/status":
            since = parse_qs(url.query).get("since", ["0"])[0]
            return self._json(JOB.snapshot(int(since) if since.isdigit() else 0))
        entry = FILES.get(url.path)
        if not entry:
            return self._send(404, "not found", "text/plain")
        try:
            with open(os.path.join(HERE, entry[0]), "rb") as fh:
                return self._send(200, fh.read(), entry[1])
        except OSError:
            return self._send(404, "not built yet - run a refresh", "text/plain")

    def do_POST(self):
        LAST_SEEN[0] = time.time()
        if not (self._allowed_host() and self._allowed_origin()):
            return self._send(403, "not from here", "text/plain")
        if urlparse(self.path).path != "/api/refresh":
            return self._send(404, "not found", "text/plain")
        started = JOB.start()
        return self._json(dict(JOB.snapshot(), started_now=started))


def idle_watch(server):
    """Shut down after a quiet half hour, never in the middle of a refresh."""
    while True:
        time.sleep(30)
        quiet = time.time() - LAST_SEEN[0] > IDLE_MINUTES * 60
        if quiet and JOB.state != "running":
            server.shutdown()
            return


def serve(port=PORT):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=idle_watch, args=(server,), daemon=True).start()
    return server


def main():
    try:
        server = serve()
    except OSError:
        print("Port %d is taken - Plinth may already be running." % PORT)
        return 1
    print("Plinth is at http://127.0.0.1:%d/" % PORT)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
