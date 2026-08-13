@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0harness\scripts\quickstart.ps1" %*
set "BUZZ_EXIT_CODE=%ERRORLEVEL%"

if "%~1"=="" pause
exit /b %BUZZ_EXIT_CODE%
