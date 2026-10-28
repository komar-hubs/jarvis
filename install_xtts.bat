@echo off
rem ASCII only: cmd.exe misreads UTF-8 Russian text in .bat files. The installer itself is install.ps1.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Xtts
if errorlevel 1 pause
