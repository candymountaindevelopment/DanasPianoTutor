@echo off
rem Double-click launcher for Danas Piano Tutor.
rem Picks a Python that has the dependencies installed (3.14 on this machine),
rem regardless of which interpreter the .py file association points at.
setlocal
cd /d "%~dp0"

set "PY="
for %%V in (3.14 3.13 3.12) do (
    if not defined PY (
        py -%%V -c "import PyQt6, numpy" >nul 2>&1 && set "PY=py -%%V"
    )
)
if not defined PY (
    python -c "import PyQt6, numpy" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo No Python 3.12+ with PyQt6 and numpy was found.
    echo Install the requirements with:   py -3.14 -m pip install -r requirements.txt
    pause
    exit /b 1
)

%PY% teach.py %*
if errorlevel 1 (
    echo.
    echo Danas Piano Tutor exited with an error ^(see above^).
    pause
)
endlocal
