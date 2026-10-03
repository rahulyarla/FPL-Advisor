@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
    set "FPL_PY=python"
) else (
    set "FPL_PY=py -3"
)
if not exist ".venv\Scripts\python.exe" (
    %FPL_PY% -m venv .venv
    if errorlevel 1 goto :failed
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m fpl_advisor serve --open --auto-hours 12
pause
exit /b 0
:failed
echo Setup did not finish. Install Python 3.11 or newer from python.org, then try again.
pause
exit /b 1
