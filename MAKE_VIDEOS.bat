@echo off
cd /d "%~dp0"
set "PY=python"
py -3 --version >nul 2>nul && set "PY=py -3"
title Video Factory - Making videos
echo.
echo  The factory is making your videos. This takes about 1 minute per video...
echo.
%PY% -m factory.make_videos
echo.
echo  Opening the review page...
%PY% -m factory.dashboard
