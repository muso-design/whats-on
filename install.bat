@echo off
rem Put Plinth on this PC: a launch icon, a refresh icon, and - if you say
rem yes when asked - a refresh that runs by itself every day.
rem
rem   install.bat          set it up
rem   install.bat remove   take it all away again
setlocal EnableExtensions
cd /d "%~dp0"
title Plinth - setup

set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY (
  echo.
  echo   Python is not installed on this computer.
  echo   Get it from  https://www.python.org/downloads/
  echo   and tick "Add python.exe to PATH" while installing.
  echo.
  pause
  exit /b 1
)

%PY% install.py %*
