@echo off
rem HIRAKU: start the screen's server and open it in the browser (double-click this file).
rem The Anthropic API key is read only from this window's environment (ANTHROPIC_API_KEY).
rem When the window has none, this file asks for it once with hidden typing; the key stays in this
rem window only and is never written anywhere. Without it, replays and scripted runs still work.
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo HIRAKU: .venv\Scripts\python.exe was not found. Make the venv first, as the README says.
  pause
  exit /b 1
)
if not defined ANTHROPIC_API_KEY call :ask_key
"%PY%" -m hiraku.webserve --open
if errorlevel 1 (
  echo.
  echo HIRAKU: the screen's server did not start - see the line above.
  echo If HIRAKU is already open in another window, use that window, or close it and double-click this file again.
  pause
)
exit /b

:ask_key
echo.
echo To run with Claude, paste your Anthropic API key below and press Enter. What you paste is not shown.
echo To only watch saved records or replay the fixed steps, just press Enter.
echo The key stays in this window only and is never saved anywhere.
for /f "usebackq delims=" %%k in (`powershell -NoProfile -Command "$s = Read-Host 'API key' -AsSecureString; $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s); try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b).Trim() } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }"`) do set "ANTHROPIC_API_KEY=%%k"
if defined ANTHROPIC_API_KEY echo Key received: Claude runs can be started from the screen.
if not defined ANTHROPIC_API_KEY echo No key: saved records and fixed-step replays only.
echo.
exit /b 0
