@echo off
cd /d "%~dp0"
set "PY=python"
py -3 --version >nul 2>nul && set "PY=py -3"
title Video Factory - Setup
%PY% --version >nul 2>nul || (
  echo.
  echo  Python is not installed yet.
  echo  1. The download page will open now. Install Python 3.12 or newer.
  echo  2. IMPORTANT: tick the box "Add python.exe to PATH" on the first screen.
  echo  3. Then double-click SETUP.bat again.
  start https://www.python.org/downloads/
  pause
  exit /b
)
echo.
echo  Installing the factory's tools (takes 1-3 minutes)...
echo.
%PY% -m pip install --upgrade pip
%PY% -m pip install -r requirements.txt
if errorlevel 1 (echo. & echo  Something went wrong. Check your internet and run SETUP.bat again. & pause & exit /b)
echo.
echo  ===============================================
echo   Setup complete! Next: double-click MAKE_VIDEOS.bat
echo  ===============================================
pause
