@echo off
cd /d "%~dp0"
set "PY=python"
py -3 --version >nul 2>nul && set "PY=py -3"
title Video Factory - YouTube sign-in
%PY% -m factory.youtube_upload
pause
