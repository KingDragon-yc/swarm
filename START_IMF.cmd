@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
python -m imf %*
if "%~1"=="" python -m imf web
