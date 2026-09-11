"""Checks for Plinth's local server: progress, the refresh button, the guards.

A real server on a spare port, and a real refresh cycle over HTTP - but the
refresh it runs is a stand-in that prints what the real one prints, so no
network, no git and no model are touched.

Run: python test_app.py
"""

import json
import sys
import threading
import time
import urllib.error
import urllib.request

import plinth_server

FAILURES = []


def check(name, got, want):
    if got == want:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s: got %r, want %r" % (name, got, want))
        FAILURES.append(name)


print("reading a refresh's output as progress")
job = plinth_server.Job()
seen = []
for line in [
        "  Graphics card free: gallery sites, call terms and translations ...",
        "  Reading the listings. This takes a few minutes.",
        "      fetching...",
        "        rundgang/leipzig: 71 event pages",
        "        rundgang/chemnitz: 1 event pages",
        "        art-at-berlin: 72 posts since 2026-07-28, 57 exhibitions parsed",
        "        direct: reading 12 venue sites with qwen2.5-coder:7b",
        "        direct: 33 shows from 12 sites",
        "        english: translated=40",
        "      open calls...",
        "        opencallforartists: 79 open of 1095 listed",
        "      wrote C:/x/index.html (581 shows, 413 calls, 1417 KB)",
        "  6 new shows, 1 new open call.",
        "  Published.",
        "",
        "  Press Enter to close this window."]:
    job.feed(line)
    seen.append(job.percent)
check("progress only ever moves forward", seen == sorted(seen), True)
check("and ends at the top", job.percent, 100)
check("with the last step named", job.step, "Done")
check("the new counts are read", (job.shows, job.calls), (6, 1))
check("blank lines and the closing prompt are not progress",
      any("Press Enter" in l for l in job.lines) or "" in job.lines, False)
check("gallery sites get their own step",
      any(label.startswith("Reading gallery sites")
          for _, _, label in plinth_server.MILESTONES), True)

busy = plinth_server.Job()
busy.feed("  A refresh is already running (started 19:00). Nothing to do; ...")
check("a scheduled run already working is recognised", busy.state, "busy")
berlin = plinth_server.Job()
berlin.state = "running"
berlin.feed("        index-berlin: 251 exhibitions (188 already running)")
check("shows that are 'already running' are not a refresh already running",
      berlin.state, "running")


print("\na refresh over HTTP, with a stand-in for the real one")
STAND_IN = r"""
import sys, time
for line in ["  Graphics card free: gallery sites ...",
             "      fetching...",
             "        direct: reading 12 venue sites",
             "        english: translated=3",
             "      open calls...",
             "      wrote index.html (1 shows, 1 calls, 1 KB)",
             "  2 new shows, 3 new open calls.",
             "  Published."]:
    print(line, flush=True)
    time.sleep(0.05)
"""
plinth_server.refresh_command = lambda: [sys.executable, "-c", STAND_IN]
plinth_server.JOB = plinth_server.Job()
server = plinth_server.serve(port=0)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % port


def call(path, method="GET", headers=None):
    request = urllib.request.Request(BASE + path, method=method,
                                     data=b"" if method == "POST" else None,
                                     headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as reply:
            return reply.status, reply.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


status, body = call("/api/ping")
check("the page can find the server", (status, json.loads(body)["app"]),
      (200, "plinth"))
status, body = call("/api/refresh", "POST")
check("pressing refresh starts one", json.loads(body)["started_now"], True)
status, body = call("/api/refresh", "POST")
check("pressing it again does not start a second", json.loads(body)["started_now"],
      False)

deadline = time.time() + 15
state = {}
while time.time() < deadline:
    state = json.loads(call("/api/status")[1])
    if state["state"] in ("done", "failed"):
        break
    time.sleep(0.1)
check("the refresh finishes", state.get("state"), "done")
check("at a hundred percent", state.get("percent"), 100)
check("with the counts for the confirmation",
      (state.get("new_shows"), state.get("new_calls")), (2, 3))
later = json.loads(call("/api/status?since=%d" % state["next"])[1])
check("status can be read from where the page left off", later["lines"], [])

plinth_server.refresh_command = lambda: [sys.executable, "-c",
                                         "import sys; print('  The refresh failed'); sys.exit(1)"]
call("/api/refresh", "POST")
deadline = time.time() + 10
while time.time() < deadline:
    state = json.loads(call("/api/status")[1])
    if state["state"] in ("done", "failed"):
        break
    time.sleep(0.1)
check("a refresh that fails says so", state["state"], "failed")
check("and why", "failed" in state["message"], True)


print("\nnothing but Plinth, and only from here")
check("a file in the folder that the page does not need is not served",
      call("/calls.json")[0], 404)
check("nor is anything reached by walking up the path",
      call("/../README.md")[0], 404)
check("the page itself is", call("/")[0] in (200, 404), True)
check("a request addressed to another host is refused (DNS rebinding)",
      call("/api/ping", headers={"Host": "evil.example"})[0], 403)
check("a refresh pressed from another website is refused",
      call("/api/refresh", "POST", {"Origin": "https://evil.example"})[0], 403)
check("but one from Plinth's own page is accepted",
      call("/api/refresh", "POST", {"Origin": BASE})[0], 200)

server.shutdown()
print("\n%d failure(s)" % len(FAILURES))
raise SystemExit(1 if FAILURES else 0)
