"""Put Plinth on this PC: a launch icon, a refresh icon, and the schedule if
you want it.

  install.bat            icons on the Desktop and in the Start menu, then asks
                         whether to refresh by itself every day
  install.bat remove     takes all of it away again

  Plinth           opens the hub in its own window - Chrome or Edge in app
                   mode, so it looks and behaves like an app, with the same
                   saved shows and tracked calls as the browser
  Refresh Plinth   refreshes everything now, shows its progress, publishes,
                   and opens the result once the site has it

The schedule is a Windows Task Scheduler task for your account only, at 07:00
and 19:00, catching up at the next login if the PC was off. It is only ever
added when you say yes.

Run: python install.py [--schedule | --no-schedule | remove]
"""

import base64
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scheduled_refresh  # noqa: E402

SITE = scheduled_refresh.SITE
TASK = "Plinth - scheduled refresh"
FOLDER = "Plinth"             # in the Start menu
OLD_SHORTCUT = "What's on.url"


def ps_quote(text):
    return "'" + str(text).replace("'", "''") + "'"


def powershell(script):
    """Run a PowerShell script without any quoting passing through cmd."""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                             "-EncodedCommand", encoded],
                            capture_output=True, text=True)
    return result.returncode, (result.stdout + result.stderr).strip()


def interpreter(windowless):
    """python.exe for a window you watch, pythonw.exe for one you do not."""
    folder = os.path.dirname(sys.executable)
    name = "pythonw.exe" if windowless else "python.exe"
    path = os.path.join(folder, name)
    return path if os.path.exists(path) else sys.executable


def place_shortcuts():
    browser = scheduled_refresh.app_browser()
    icon = os.path.join(HERE, "icon.ico")
    refresh_icon = os.path.join(HERE, "icon-refresh.ico")
    script = os.path.join(HERE, "scheduled_refresh.py")
    lines = [
        "$shell = New-Object -ComObject WScript.Shell",
        "$desktop = [Environment]::GetFolderPath('Desktop')",
        "$programs = Join-Path ([Environment]::GetFolderPath('Programs')) %s"
        % ps_quote(FOLDER),
        "New-Item -ItemType Directory -Force $programs | Out-Null",
        "foreach ($dir in @($desktop, $programs)) {",
    ]
    if browser:
        lines += [
            "  $s = $shell.CreateShortcut((Join-Path $dir 'Plinth.lnk'))",
            "  $s.TargetPath = %s" % ps_quote(browser),
            "  $s.Arguments = %s" % ps_quote("--app=" + SITE),
            "  $s.IconLocation = %s" % ps_quote(icon + ",0"),
            "  $s.Description = 'Shows to see and open calls to enter'",
            "  $s.Save()",
        ]
    lines += [
        "  $r = $shell.CreateShortcut((Join-Path $dir 'Refresh Plinth.lnk'))",
        "  $r.TargetPath = %s" % ps_quote(interpreter(windowless=False)),
        "  $r.Arguments = %s" % ps_quote('"%s" --manual' % script),
        "  $r.WorkingDirectory = %s" % ps_quote(HERE),
        "  $r.IconLocation = %s" % ps_quote(refresh_icon + ",0"),
        "  $r.Description = 'Refresh Plinth now and open it'",
        "  $r.Save()",
        "}",
        "Write-Output $desktop",
    ]
    code, output = powershell("\n".join(lines))
    if code != 0:
        return False, output
    desktop = output.splitlines()[-1] if output else ""
    if not browser:
        # No Chrome or Edge to open it as an app: a plain link, same icon.
        for folder in (desktop, _programs_folder()):
            with open(os.path.join(folder, "Plinth.url"), "w", encoding="utf-8") as fh:
                fh.write("[InternetShortcut]\nURL=%s\nIconFile=%s\nIconIndex=0\n"
                         % (SITE, icon))
    _retire_old_shortcut(desktop)
    return True, desktop


def _programs_folder():
    code, output = powershell(
        "Join-Path ([Environment]::GetFolderPath('Programs')) %s" % ps_quote(FOLDER))
    return output.strip() if code == 0 else HERE


def _retire_old_shortcut(desktop):
    """Remove the old "What's on" link - but only if it is the one we made."""
    path = os.path.join(desktop or "", OLD_SHORTCUT)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            if SITE not in fh.read():
                return
        os.remove(path)
        print("  Replaced the old \"What's on\" shortcut.")
    except OSError:
        pass


def register_schedule():
    script = os.path.join(HERE, "scheduled_refresh.py")
    code, output = powershell("\n".join([
        "$a = New-ScheduledTaskAction -Execute %s -Argument %s -WorkingDirectory %s"
        % (ps_quote(interpreter(windowless=True)), ps_quote('"%s"' % script),
           ps_quote(HERE)),
        "$t = @((New-ScheduledTaskTrigger -Daily -At 07:00), "
        "(New-ScheduledTaskTrigger -Daily -At 19:00))",
        "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries "
        "-DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew "
        "-ExecutionTimeLimit (New-TimeSpan -Hours 2)",
        "Register-ScheduledTask -TaskName %s -Action $a -Trigger $t -Settings $s "
        "-Description 'Refreshes Plinth, reading gallery sites, open-call terms and "
        "translations with the local model when the graphics card is free, then "
        "publishes it.' -Force | Out-Null" % ps_quote(TASK),
    ]))
    return code == 0, output


def remove():
    code, output = powershell("\n".join([
        "Unregister-ScheduledTask -TaskName %s -Confirm:$false "
        "-ErrorAction SilentlyContinue" % ps_quote(TASK),
        "$desktop = [Environment]::GetFolderPath('Desktop')",
        "$programs = Join-Path ([Environment]::GetFolderPath('Programs')) %s"
        % ps_quote(FOLDER),
        "foreach ($name in @('Plinth.lnk', 'Refresh Plinth.lnk', 'Plinth.url')) {",
        "  Remove-Item -LiteralPath (Join-Path $desktop $name) -ErrorAction SilentlyContinue",
        "}",
        "Remove-Item -LiteralPath $programs -Recurse -ErrorAction SilentlyContinue",
    ]))
    print("\n  Removed the icons and the schedule. Nothing runs on its own any more.\n"
          if code == 0 else "\n  Could not remove everything: %s\n" % output[:200])
    return code


def ask(question):
    try:
        answer = input(question).strip().lower()
    except EOFError:
        return None               # nobody at the keyboard: decide nothing
    return answer in ("", "y", "yes", "j", "ja")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "remove" in argv:
        return remove()

    print("\n  Setting up Plinth\n")
    ok, where = place_shortcuts()
    if not ok:
        print("  Windows did not accept the shortcuts: %s" % where[:200])
        return 1
    print("  Added \"Plinth\" and \"Refresh Plinth\" to the Desktop and the Start menu.")

    if "--schedule" in argv:
        wanted = True
    elif "--no-schedule" in argv:
        wanted = False
    else:
        print("\n  Plinth can also refresh by itself at 07:00 and 19:00 - or at your")
        print("  next login if the PC was off - without opening any window.")
        wanted = ask("  Set that up? [Y/n] ")
    if wanted:
        ok, output = register_schedule()
        print("  Done: it refreshes by itself every day." if ok else
              "  Windows did not accept the schedule: %s" % output[:200])
    elif wanted is False:
        print("  No schedule. Run install.bat again any time to add it.")

    print("\n  To take it all away again: install.bat remove\n")
    if "--schedule" not in argv and "--no-schedule" not in argv:
        try:
            input("  Press Enter to close this window.")
        except EOFError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
