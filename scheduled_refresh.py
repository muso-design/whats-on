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

Run by hand: python scheduled_refresh.py
"""

import os
import subprocess
import sys
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


def main():
    log("\n==== %s ====" % datetime.now().isoformat(timespec="seconds"))
    try:
        return refresh()
    except Exception as exc:                                   # noqa: BLE001
        log("stopped: %r" % exc)
        return 1
    finally:
        trim_log()


def refresh():
    if run("git", "pull", "--rebase", "--autostash") != 0:
        run("git", "rebase", "--abort")
        log("Could not start from what GitHub has; skipped tonight.")
        return 1

    env = dict(os.environ)
    placement = model_placement()
    if placement == "gpu":
        log("Model is on the graphics card; reading gallery sites and call terms.")
    elif placement == "cpu":
        unload_model()
        env["OLLAMA_HOST"] = NO_MODEL
        log("The graphics card is busy, so the model would run on the processor "
            "and slow the PC down. Model steps wait for the next run; "
            "everything else refreshes.")
    else:
        log("No model reachable; refreshing what needs none.")

    # sys.executable is pythonw under the scheduler, so this stays windowless.
    if run(sys.executable, os.path.join(HERE, "update.py"),
           env=env, polite=True) != 0:
        log("The refresh failed; nothing published.")
        return 1

    present = [name for name in DATA_FILES if os.path.exists(os.path.join(HERE, name))]
    run("git", "add", "--", *present)
    if run("git", "diff", "--cached", "--quiet") == 0:
        log("Nothing changed.")
        return 0
    run("git", "commit", "-q", "-m",
        "what's on, local refresh %s" % datetime.now().strftime("%Y-%m-%d"))

    if run("git", "push") != 0:
        # GitHub moved on while this ran. Put tonight on top and try once more.
        if run("git", "pull", "--rebase") != 0 or run("git", "push") != 0:
            run("git", "rebase", "--abort")
            log("Could not publish; tonight's refresh is kept here and goes "
                "out with the next one that can.")
            return 1
    log("Published.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
