@echo off
setlocal
pushd "%~dp0" || exit /b 1
if not exist ".venv\Scripts\python.exe" (
    echo Setting up the demo for the first time...
    py -3.12 -m venv .venv
    if errorlevel 1 goto setup_failed
)
".venv\Scripts\python.exe" -c "import tkinter, PIL.ImageTk; import sys; sys.exit(sys.version_info[:2] != (3, 12) or PIL.__version__ != '12.0.0')" >nul 2>&1
if errorlevel 1 (
    echo Installing the demo dependency...
    ".venv\Scripts\python.exe" -m pip install --require-hashes --only-binary=:all: -r requirements.lock
    if errorlevel 1 goto setup_failed
    ".venv\Scripts\python.exe" -c "import tkinter, PIL.ImageTk; import sys; sys.exit(sys.version_info[:2] != (3, 12) or PIL.__version__ != '12.0.0')"
    if errorlevel 1 goto setup_failed
)
start "" "%CD%\.venv\Scripts\pythonw.exe" "%CD%\scripts\aip_demo.py"
if errorlevel 1 goto setup_failed
popd
exit /b 0

:setup_failed
echo.
echo The demo needs 64-bit Python 3.12 with Tcl/Tk and pip. Install Python, then reopen this file.
echo First-time setup also needs access to the pinned Pillow wheel.
pause
popd
exit /b 1
