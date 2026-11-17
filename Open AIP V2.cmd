@echo off
setlocal
set "aip_version=2"
set "aip_port=8765"
call "%~dp0scripts\open_workbench.cmd" %*
exit /b %errorlevel%
