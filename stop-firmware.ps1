# Baja los contenedores del enjambre (modo firmware).  Uso:  .\stop-firmware.ps1
Set-Location $PSScriptRoot
Write-Host "Bajando los ArduCopter (Docker)..."
docker compose -f sitl\docker-compose.yml down
Write-Host "Listo. (El modo simulador -py run.py- no usa Docker.)"
