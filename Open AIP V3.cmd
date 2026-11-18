@echo off
setlocal
set "aip_version=3"
set "aip_port=8766"
call "%~dp0scripts\open_workbench.cmd" %*
exit /b %errorlevel%
