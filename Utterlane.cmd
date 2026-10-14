@echo off
rem Utterlane Live - double-click to start. First run: install.ps1 is run automatically.
cd /d "%~dp0"
if not exist ".venv\Scripts\utterlane.exe" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
  if errorlevel 1 ( pause & exit /b 1 )
)
".venv\Scripts\utterlane.exe" %*
if errorlevel 1 pause
