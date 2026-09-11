"""Open Plinth: start its local server if it is not running, then open it.

This is what the Plinth icon on the Desktop and in the Start menu runs. It is
a .pyw, run by pythonw, so no console window appears. The page opens in its
own window - Chrome or Edge in app mode - at the local address, where the
Refresh button can actually refresh.

If the local server cannot start (the port is taken by something else, say),
the published copy opens instead, so the icon always shows you Plinth.
"""

import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plinth_server        # noqa: E402
import scheduled_refresh    # noqa: E402

LOCAL = "http://127.0.0.1:%d/" % plinth_server.PORT


def server_answers():
    try:
        with urllib.request.urlopen(LOCAL + "api/ping", timeout=1) as reply:
            return b'"plinth"' in reply.read()
    except Exception:                                          # noqa: BLE001
        return False


def start_server():
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
             | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    subprocess.Popen([pythonw if os.path.exists(pythonw) else sys.executable,
                      os.path.join(HERE, "plinth_server.py")],
                     cwd=HERE, creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    for _ in range(40):
        if server_answers():
            return True
        time.sleep(0.2)
    return False


def main():
    url = LOCAL if (server_answers() or start_server()) else scheduled_refresh.SITE
    if "--refresh" in sys.argv[1:] and url == LOCAL:
        url += "?refresh=1"
    browser = scheduled_refresh.app_browser()
    if browser:
        subprocess.Popen([browser, "--app=" + url])
    else:
        import webbrowser
        webbrowser.open(url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
