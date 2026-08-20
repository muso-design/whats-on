@echo off
rem Fetch the latest listings and open the hub on this computer.
rem
rem   launch.bat            refresh if the listings are stale, then open
rem   launch.bat refresh    always fetch first
rem   launch.bat open       just open, do not fetch
rem
rem The published hub at https://muso-design.github.io/whats-on/ refreshes
rem itself daily, so most of the time you want the Desktop icon instead.
rem This is for looking at it offline, or seeing new listings straight away.
setlocal EnableExtensions
cd /d "%~dp0"
title What's on

echo.
echo   ============================================
echo      What's on - exhibitions near you
echo   ============================================
echo.

rem ---- find Python -------------------------------------------------------
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY (
  echo   Python is not installed on this computer.
  echo.
  echo   Get it from  https://www.python.org/downloads/
  echo   and tick "Add python.exe to PATH" while installing.
  echo.
  pause
  exit /b 1
)

rem ---- dependencies, once ------------------------------------------------
%PY% -c "import requests, bs4, lxml" >nul 2>&1
if errorlevel 1 (
  echo   Setting things up for the first time. This takes a minute...
  echo.
  %PY% -m pip install --quiet --disable-pip-version-check -r requirements.txt
  if errorlevel 1 (
    echo   Setup failed. Check your internet connection and try again.
    echo.
    pause
    exit /b 1
  )
)

rem ---- refresh? ----------------------------------------------------------
set "MODE=%~1"
if /i "%MODE%"=="open" goto serve
if /i "%MODE%"=="refresh" goto refresh
if not exist "index.html" goto refresh

rem Exit code 1 from this check means the listings are more than three days old.
%PY% -c "import state,datetime,sys;lr=state.load().get('last_run');sys.exit(0 if lr and (datetime.datetime.now()-datetime.datetime.fromisoformat(lr)).days<3 else 1)" >nul 2>&1
if errorlevel 1 (
  echo   Your listings are a few days old. Fetching the latest.
  goto refresh
)
echo   Listings are up to date.
goto serve

:refresh
echo.
echo   Looking for new exhibitions. This takes a few minutes -
echo   it reads four sites and is deliberately gentle with them.
echo.
%PY% update.py
if errorlevel 1 (
  echo.
  echo   Could not finish the update. Opening what you already have.
)

:serve
if not exist "index.html" (
  echo.
  echo   Nothing to show yet. Run:  launch.bat refresh
  echo.
  pause
  exit /b 1
)

echo.
echo   ============================================
echo      Opening in your browser.
echo      Leave this window open while you browse.
echo      Close it when you are done.
echo   ============================================
echo.
start "" "http://localhost:8000/"
%PY% -m http.server 8000 --bind 127.0.0.1 >nul 2>&1
