@echo off
rem Open the hub. Refreshes first if the data is stale.
rem   launch.bat            open, refreshing only if needed
rem   launch.bat refresh    always fetch fresh listings first
rem   launch.bat open       never refresh, just open
setlocal EnableExtensions
cd /d "%~dp0"
title What's on

rem ---- find Python -------------------------------------------------------
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY (
  echo.
  echo Python was not found.
  echo Install it from https://www.python.org/downloads/ and tick
  echo "Add python.exe to PATH" during setup, then run this again.
  echo.
  pause
  exit /b 1
)

rem ---- dependencies, once ------------------------------------------------
%PY% -c "import requests, bs4, lxml" >nul 2>&1
if errorlevel 1 (
  echo Installing dependencies. This happens once and takes a minute.
  %PY% -m pip install --quiet --disable-pip-version-check -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Could not install the dependencies.
    pause
    exit /b 1
  )
)

rem ---- decide whether to refresh -----------------------------------------
set "MODE=%~1"
if /i "%MODE%"=="open" goto serve
if /i "%MODE%"=="refresh" goto refresh
if not exist "index.html" goto refresh

rem Stale after three days. Exit code 1 from this check means "go and fetch".
%PY% -c "import state,datetime,sys;lr=state.load().get('last_run');sys.exit(0 if lr and (datetime.datetime.now()-datetime.datetime.fromisoformat(lr)).days<3 else 1)" >nul 2>&1
if errorlevel 1 goto refresh
goto serve

:refresh
echo.
echo Fetching what's on. A few minutes the first time.
echo.
%PY% update.py
if errorlevel 1 (
  echo.
  echo The update did not finish. Opening the last version instead.
  echo.
)

:serve
rem Serve over localhost rather than opening the file directly: saved shows
rem and offline support both need a real origin, which file:// does not give.
if not exist "index.html" (
  echo No page to open yet. Run: launch.bat refresh
  pause
  exit /b 1
)
echo.
echo Opening http://localhost:8000/
echo Close this window when you are done.
echo.
start "" "http://localhost:8000/"
%PY% -m http.server 8000 --bind 127.0.0.1
