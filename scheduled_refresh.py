"""The nightly refresh on this PC, for Windows Task Scheduler to run.

GitHub refreshes the published hub every morning, but it has no local model,
so the things that need one only happen here: reading gallery sites directly
(REITER, Koenitz and everything else in venues.yaml) and reading the terms of
new open calls. Until now they happened only when launch.bat was opened, and
their results reached the phone only when someone pushed them by hand.

This runs the whole pipeline and publishes the result. It is started by
pythonw, so no window opens, and everything it does goes to refresh.log.

Two rules make it safe to run unattended next to the GitHub job:

  it starts from what GitHub has, so the two refreshes build on each other
  rather than race; if that cannot be done cleanly it does nothing at all -
  a skipped night shows on the page, a tangled repository would not

  it commits only the files the refresh writes, so code being edited in the
  same folder is never committed by a robot

And one rule so it is safe to run next to you: the model is only used when it
fits on the graphics card. On the first evening this ran, ZBrush and a game had
7 of the card's 8 GB, Ollama put the model 96% on the processor, and a job that
takes three minutes on the card was on course for an hour of pegged CPU. So it
checks first. With the card busy, the model steps wait for the next run and
everything else refreshes and publishes as normal.

The same script is the manual refresh. With --manual - which is what the
"Refresh Plinth" icon runs - it shows its progress in a window, waits until the
published site carries the build it just made, and then opens Plinth. One
path for both, so the button is exactly as tested as the schedule, and a lock
keeps a click and a scheduled run from ever writing at the same time.

Run by hand: python scheduled_refresh.py [--manual]
"""

import os
import re
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "refresh.log")
LOG_KEEP = 4000                   # lines; the log is for the last few weeks

# Everything update.py writes, and nothing else.
DATA_FILES = [
    "state.json", "calls.json", "ocfa_cache.json", "index.html", "sw.js",
    "manifest.webmanifest", "icon-192.png", "icon-512.png", "icon.ico",
    "llm_cache.json", "translations.json", "artists.json", "venues.json",
]

# No console window for git either, and the refresh itself yields to
# whatever you are doing.
_QUIET = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_POLITE = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)

# Below this share of the model in video memory, it is mostly on the CPU.
GPU_SHARE = 0.8
# Where update.py will find no model at all.
NO_MODEL = "http://127.0.0.1:9"


def log(message):
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(message.rstrip() + "\n")


def run(*command, env=None, polite=False):
    """Run a command in this folder, log what it said, return its exit code."""
    result = subprocess.run(command, cwd=HERE, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", env=env,
                            creationflags=_QUIET | (_POLITE if polite else 0))
    output = (result.stdout + result.stderr).strip()
    if output:
        log(output)
    return result.returncode


def model_placement():
    """'gpu', 'cpu', or None when there is no model to ask.

    Loads the model with a one-token prompt and asks Ollama where it put it,
    which is the only reliable answer: free video memory depends on what else
    is open, and Ollama decides the split itself.
    """
    sys.path.insert(0, HERE)
    import llm
    import requests
    if not llm.available():
        return None
    try:
        requests.post(llm.ENDPOINT + "/api/generate", timeout=300, json={
            "model": llm.MODEL, "prompt": "ok", "stream": False,
            "options": {"num_predict": 1}})
        loaded = requests.get(llm.ENDPOINT + "/api/ps", timeout=15).json()
    except Exception:                                          # noqa: BLE001
        return None
    for model in loaded.get("models") or []:
        if llm.MODEL in (model.get("name"), model.get("model")):
            size = model.get("size") or 0
            if not size:
                return None
            return "gpu" if (model.get("size_vram") or 0) >= GPU_SHARE * size else "cpu"
    return None


def unload_model():
    """Hand the memory back rather than holding it for the keep-alive time."""
    try:
        import llm
        import requests
        requests.post(llm.ENDPOINT + "/api/generate", timeout=30,
                      json={"model": llm.MODEL, "keep_alive": 0})
    except Exception:                                          # noqa: BLE001
        pass


def trim_log():
    try:
        with open(LOG, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return
    if len(lines) > LOG_KEEP:
        with open(LOG, "w", encoding="utf-8") as fh:
            fh.writelines(lines[-LOG_KEEP:])


# --------------------------------------------------------------------------
# one refresh at a time
# --------------------------------------------------------------------------

LOCK = os.path.join(HERE, "refresh.lock")
LOCK_STALE_HOURS = 3          # a lock older than any real run is a crash's


def _alive(pid):
    """Is this process still running?

    Not os.kill(pid, 0): on Windows that does not probe a process, it ends
    it. The kernel is asked instead.
    """
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    kernel = ctypes.windll.kernel32
    handle = kernel.OpenProcess(0x1000, False, pid)   # query limited information
    if not handle:
        return False
    code = ctypes.c_ulong()
    ok = kernel.GetExitCodeProcess(handle, ctypes.byref(code))
    kernel.CloseHandle(handle)
    return bool(ok) and code.value == 259             # STILL_ACTIVE


def take_lock(path=LOCK):
    """None if this run may go ahead; otherwise when the running one began."""
    now = datetime.now()
    try:
        with open(path, encoding="utf-8") as fh:
            pid, started = fh.read().strip().split("|", 1)
        age = now - datetime.fromisoformat(started)
        if _alive(int(pid)) and age.total_seconds() < LOCK_STALE_HOURS * 3600:
            return started
    except (OSError, ValueError):
        pass
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("%d|%s" % (os.getpid(), now.isoformat(timespec="seconds")))
    return None


def release_lock(path=LOCK):
    try:
        with open(path, encoding="utf-8") as fh:
            if fh.read().split("|", 1)[0] != str(os.getpid()):
                return                 # someone else's; not ours to remove
        os.remove(path)
    except OSError:
        pass


# --------------------------------------------------------------------------
# the manual refresh: the same run, with someone watching
# --------------------------------------------------------------------------

SITE = "https://muso-design.github.io/whats-on/"
LIVE_WAIT = 300               # seconds to wait for GitHub Pages to publish
MANUAL = False
OPEN_AFTER = True             # --no-open: refresh with progress, open nothing


def say(message):
    """Log it, and when someone is watching, show it too."""
    log(message)
    if MANUAL:
        print("  " + message, flush=True)


def build_id(html):
    match = re.search(r'<meta name="plinth-build" content="([^"]+)"', html or "")
    return match.group(1) if match else None


def wait_until_live(wanted, timeout=LIVE_WAIT):
    """Poll the published page until it carries this build, or give up."""
    import requests
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            page = requests.get(SITE + "index.html", timeout=20,
                                params={"fresh": int(time.time())},
                                headers={"Cache-Control": "no-cache"}).text
            if build_id(page) == wanted:
                return True
        except Exception:                                      # noqa: BLE001
            pass
        if MANUAL:
            print(".", end="", flush=True)
        time.sleep(10)
    return False


BROWSERS = [
    os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
    os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
]


def app_browser():
    """Chrome or Edge, which can open the hub as its own window."""
    return next((path for path in BROWSERS if os.path.exists(path)), None)


def open_hub():
    if not OPEN_AFTER:
        return
    browser = app_browser()
    if browser:
        subprocess.Popen([browser, "--app=" + SITE])
    else:
        import webbrowser
        webbrowser.open(SITE)


def new_counts(output):
    """(new shows, new calls) from update.py's own report."""
    found = [int(n) for n in re.findall(r"(\d+) new since the last run", output or "")]
    return (found + [0, 0])[:2]


def run_update(env):
    """update.py, streamed to the window in manual mode."""
    command = [sys.executable, os.path.join(HERE, "update.py")]
    env = dict(env, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    if not MANUAL:
        result = subprocess.run(command, cwd=HERE, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", env=env,
                                creationflags=_QUIET | _POLITE)
        output = result.stdout + result.stderr
        log(output.strip())
        return result.returncode, output
    process = subprocess.Popen(command, cwd=HERE, env=env, text=True,
                               encoding="utf-8", errors="replace",
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               creationflags=_POLITE)
    lines = []
    for line in process.stdout:
        lines.append(line)
        # The top-level steps, not the per-venue detail beneath them.
        if line.strip() and not line.startswith("    "):
            print("      " + line.rstrip()[:96], flush=True)
    process.wait()
    output = "".join(lines)
    log(output.strip())
    return process.returncode, output


# --------------------------------------------------------------------------

def main(argv=None):
    global MANUAL, OPEN_AFTER
    argv = sys.argv[1:] if argv is None else argv
    MANUAL = "--manual" in argv
    OPEN_AFTER = "--no-open" not in argv
    if MANUAL:
        os.system("title Plinth - refreshing")
        print("\n  Plinth - refreshing everything now\n", flush=True)
    log("\n==== %s%s ====" % (datetime.now().isoformat(timespec="seconds"),
                              " (manual)" if MANUAL else ""))
    busy_since = take_lock()
    if busy_since:
        say("A refresh is already running (started %s). Nothing to do; "
            "it will publish when it finishes." % busy_since[11:16])
        code = 0
    else:
        try:
            code = refresh()
        except Exception as exc:                               # noqa: BLE001
            say("Stopped: %r" % exc)
            code = 1
        finally:
            release_lock()
            trim_log()
    if MANUAL:
        try:
            input("\n  Press Enter to close this window.")
        except EOFError:
            pass
    return code


def refresh():
    if run("git", "pull", "--rebase", "--autostash") != 0:
        run("git", "rebase", "--abort")
        say("Could not start from what GitHub has; nothing was changed.")
        return 1

    env = dict(os.environ)
    placement = model_placement()
    if placement == "gpu":
        say("Graphics card free: gallery sites, call terms and translations "
            "will be read by the local model.")
    elif placement == "cpu":
        unload_model()
        env["OLLAMA_HOST"] = NO_MODEL
        say("The graphics card is busy, so the model would run on the processor "
            "and slow the PC down. Gallery sites, call terms and translations "
            "wait for a run when it is free; everything else refreshes.")
    else:
        say("Ollama is not running: gallery sites, call terms and translations "
            "are skipped; everything else refreshes.")

    say("Reading the listings. This takes a few minutes.")
    code, output = run_update(env)
    if code != 0:
        say("The refresh failed; nothing was published. Details are in refresh.log.")
        return 1
    shows, calls = new_counts(output)
    say("%d new show%s, %d new open call%s." % (shows, "" if shows == 1 else "s",
                                                calls, "" if calls == 1 else "s"))

    present = [name for name in DATA_FILES if os.path.exists(os.path.join(HERE, name))]
    run("git", "add", "--", *present)
    if run("git", "diff", "--cached", "--quiet") == 0:
        say("Nothing changed since the last refresh.")
        if MANUAL:
            open_hub()
        return 0
    run("git", "commit", "-q", "-m",
        "what's on, %s refresh %s" % ("manual" if MANUAL else "local",
                                      datetime.now().strftime("%Y-%m-%d %H:%M")))

    if run("git", "push") != 0:
        # GitHub moved on while this ran. Put this on top and try once more.
        if run("git", "pull", "--rebase") != 0 or run("git", "push") != 0:
            run("git", "rebase", "--abort")
            say("Could not publish; this refresh is kept here and goes out "
                "with the next one that can.")
            return 1
    say("Published.")

    if MANUAL:
        with open(os.path.join(HERE, "index.html"), encoding="utf-8") as fh:
            wanted = build_id(fh.read())
        print("  Waiting for the site to show it (usually a minute or two) ",
              end="", flush=True)
        live = wait_until_live(wanted) if wanted else False
        print(flush=True)
        say("Live - opening Plinth." if live else
            "Still publishing; opening Plinth now, and it will update itself "
            "within a few minutes.")
        open_hub()
    return 0


if __name__ == "__main__":
    sys.exit(main())
