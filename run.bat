@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating AceGPT's private Python environment...
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -m venv .venv
    ) else (
        python -m venv .venv
    )
    if errorlevel 1 goto setup_error
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    if errorlevel 1 goto setup_error
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto setup_error
)

".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto setup_error
".venv\Scripts\python.exe" bot.py
if errorlevel 1 pause
exit /b %errorlevel%

:setup_error
echo AceGPT setup failed. Check that Python 3.11+ and an internet connection are available.
pause
exit /b 1
