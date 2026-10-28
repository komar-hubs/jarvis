@echo off
rem ASCII only (see install.bat). Records every microphone for 2 s and writes mic_report.txt.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" tools\mic_check.py
) else (
  python tools\mic_check.py
)
pause
