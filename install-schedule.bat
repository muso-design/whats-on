@echo off
rem Refresh What's on by itself on this PC, without you opening anything.
rem
rem   install-schedule.bat          set it up (07:00 and 19:00 every day)
rem   install-schedule.bat remove   take it away again
rem
rem It adds one task to Windows Task Scheduler, for your account only, that
rem runs scheduled_refresh.py with no window. If the PC is off at either time
rem it runs the next time you log in. The model is only used when it fits on
rem the graphics card, so it never grinds the processor while you work.
rem What it did each time is in refresh.log.
setlocal EnableExtensions
cd /d "%~dp0"
title What's on - scheduled refresh

set "TASK=What's on - scheduled refresh"

if /i "%~1"=="remove" (
  powershell -NoProfile -Command "Unregister-ScheduledTask -TaskName \"%TASK%\" -Confirm:$false" >nul 2>&1
  echo.
  echo   Removed. Nothing runs on its own any more.
  echo.
  pause
  exit /b 0
)

set "PYW="
for /f "delims=" %%p in ('where pythonw 2^>nul') do if not defined PYW set "PYW=%%p"
if not defined PYW (
  echo.
  echo   pythonw.exe was not found. Is Python installed with "Add to PATH"?
  echo.
  pause
  exit /b 1
)

powershell -NoProfile -Command ^
  "$a = New-ScheduledTaskAction -Execute '%PYW%' -Argument '\"%~dp0scheduled_refresh.py\"' -WorkingDirectory '%~dp0';" ^
  "$t = @((New-ScheduledTaskTrigger -Daily -At 07:00), (New-ScheduledTaskTrigger -Daily -At 19:00));" ^
  "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2);" ^
  "Register-ScheduledTask -TaskName \"%TASK%\" -Action $a -Trigger $t -Settings $s -Description 'Refreshes the What''s on hub, reading gallery sites and open-call terms with the local model when the graphics card is free, then publishes it.' -Force | Out-Null"

if errorlevel 1 (
  echo.
  echo   Windows did not accept the task. Nothing was changed.
  echo.
  pause
  exit /b 1
)
echo.
echo   Done. What's on now refreshes itself at 07:00 and 19:00,
echo   or at your next login if the PC was off then.
echo.
echo   Gallery sites and call terms are read when Ollama is running and
echo   the graphics card is free. Everything else always refreshes.
echo.
echo   To stop it:  install-schedule.bat remove
echo.
pause
