@echo off
setlocal
pushd "%~dp0.." || exit /b 1
if not exist ".venv\Scripts\python.exe" (
    echo Setting up AIP V%aip_version% for the first time...
    py -3.12 -m venv .venv
    if errorlevel 1 goto setup_failed
)
call :check_runtime >nul 2>&1
if errorlevel 1 (
    echo Installing the locked AIP V%aip_version% dependencies...
    ".venv\Scripts\python.exe" -m pip --disable-pip-version-check install --require-hashes --only-binary=:all: -r requirements-v2.lock
    if errorlevel 1 goto setup_failed
    call :check_runtime
    if errorlevel 1 goto setup_failed
)
echo.
echo AIP V%aip_version% opens in your browser at http://127.0.0.1:%aip_port%
echo Keep this window open while using the workbench. Press Ctrl+C here to stop it.
echo Drawings and corrections stay in this project folder under runs\v%aip_version%.
echo.
".venv\Scripts\python.exe" "%CD%\scripts\aip_v%aip_version%.py" %*
set "aip_exit_code=%errorlevel%"
if not "%aip_exit_code%"=="0" (
    echo.
    echo AIP V%aip_version% stopped with an error. If the address is already in use, close the previous workbench console.
    echo You can also run this launcher with --port 8770 to choose another local port.
    pause
)
popd
exit /b %aip_exit_code%

:check_runtime
".venv\Scripts\python.exe" -c "import sys, PIL, pypdfium2; from importlib.metadata import version; import winrt.windows.foundation, winrt.windows.foundation.collections, winrt.windows.globalization, winrt.windows.graphics.imaging, winrt.windows.media.ocr, winrt.windows.storage.streams; sys.exit(sys.version_info[:2] != (3, 12) or sys.maxsize <= 2**32 or PIL.__version__ != '12.0.0' or version('pypdfium2') != '5.4.0')"
exit /b %errorlevel%

:setup_failed
echo.
echo AIP V%aip_version% needs 64-bit Python 3.12 with pip. Install Python, then reopen this file.
echo First-time setup needs access to the pinned Python packages.
echo Local label reading also needs an installed Windows OCR language.
echo See docs\V%aip_version%_WORKBENCH.md for setup and troubleshooting.
pause
popd
exit /b 1
