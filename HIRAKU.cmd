@echo off
rem HIRAKU: start the screen's server and open it in the browser (double-click this file).
rem The Anthropic API key is read only from this window's environment (ANTHROPIC_API_KEY);
rem this file never writes it or asks for it. Without it, scripted runs and replays still work.
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo HIRAKU: .venv\Scripts\python.exe was not found. Make the venv first (see README).
  pause
  exit /b 1
)
"%PY%" -m hiraku.webserve --open
if errorlevel 1 pause
