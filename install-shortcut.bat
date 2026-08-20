@echo off
rem Put a "What's on" icon on the Desktop that opens the hub.
rem Run this once. It creates a shortcut and changes nothing else.
setlocal EnableExtensions
cd /d "%~dp0"
title Add What's on to the Desktop

set "SITE=https://muso-design.github.io/whats-on/"
set "LINK=%USERPROFILE%\Desktop\What's on.url"

if not exist "icon.ico" (
  echo.
  echo   The icon is missing. Run this first:
  echo       python board.py
  echo.
  pause
  exit /b 1
)

echo.
echo   Adding "What's on" to your Desktop.
echo   It opens: %SITE%
echo.

> "%LINK%" echo [InternetShortcut]
>> "%LINK%" echo URL=%SITE%
>> "%LINK%" echo IconFile=%~dp0icon.ico
>> "%LINK%" echo IconIndex=0

if exist "%LINK%" (
  echo   Done. Look for the green icon on your Desktop.
) else (
  echo   Could not write to the Desktop. Nothing was changed.
)
echo.
echo   On your phone, open the same address and choose
echo   "Add to home screen" to install it there too.
echo.
pause
