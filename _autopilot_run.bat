@echo off
cd /d "%~dp0"
set "PY=python"
py -3 --version >nul 2>nul && set "PY=py -3"
%PY% -m factory.make_videos >> "output\logs\autopilot.log" 2>&1
