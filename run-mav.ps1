# Arranca LHOK contra el enjambre de FIRMWARE REAL (ArduPilot SITL en Docker).
# Requiere el contenedor arriba:  cd sitl; docker compose up
# Uso:  .\run-mav.ps1
$env:LHOK_BACKEND = "mav"
# Endpoints por defecto (tcp 5760+10*i) — descomentar sólo si cambiaste los puertos:
# $env:LHOK_SITL = "tcp:127.0.0.1:5760,tcp:127.0.0.1:5770,tcp:127.0.0.1:5780,tcp:127.0.0.1:5790,tcp:127.0.0.1:5800,tcp:127.0.0.1:5810,tcp:127.0.0.1:5820"
Set-Location -Path $PSScriptRoot
py run.py
