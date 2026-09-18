@echo off
REM Arranca FALANGE contra el enjambre de FIRMWARE REAL (ArduPilot SITL en Docker).
REM Requiere el contenedor arriba:  cd sitl ^&^& docker compose up
cd /d "%~dp0"
set FALANGE_BACKEND=mav
py run.py
pause
