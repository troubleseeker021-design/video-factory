@echo off
cd /d "%~dp0"
schtasks /create /tn "FacelessVideoFactory" /tr "\"%~dp0_autopilot_run.bat\"" /sc daily /st 09:00 /f
echo.
echo  Autopilot is ON. Every day at 9:00 AM new videos will be made
echo  (your PC must be on). Double-click REVIEW.bat to approve them.
echo  To change the time, edit "09:00" in this file and run it again.
pause
