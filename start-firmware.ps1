# ===========================================================================
#  Lhok — MODO FIRMWARE REAL en UN SOLO COMANDO.
#  Levanta los 7 ArduCopter (Docker), espera a que estén listos y arranca Lhok.
#
#  Uso:   .\start-firmware.ps1
#  Requisito:  Docker Desktop abierto (la ballena verde, abajo a la derecha).
# ===========================================================================
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "  Lhok - modo firmware real (ArduPilot)" -ForegroundColor Cyan
Write-Host "  ----------------------------------------"

# 1) ¿Docker Desktop está corriendo?
docker info *> $null 2>&1
if ($LASTEXITCODE -ne 0) {
  Write-Host ""
  Write-Host "  [X] Docker Desktop no esta corriendo." -ForegroundColor Red
  Write-Host "      Abri 'Docker Desktop' (menu Inicio), espera la ballena VERDE y volve a correr esto."
  exit 1
}
Write-Host "  [1/3] Docker OK. Levantando los 7 ArduCopter..."
docker compose -f sitl\docker-compose.yml up -d | Out-Null

# 2) esperar a que los 7 puertos MAVLink abran, luego dar tiempo al EKF/GPS
function Test-Port($p) {
  try { $c = New-Object Net.Sockets.TcpClient; $c.Connect("127.0.0.1", $p); $c.Close(); return $true }
  catch { return $false }
}
Write-Host "  [2/3] Esperando a que el firmware bootee..."
$ready = $false
for ($i = 0; $i -lt 40; $i++) {
  $open = 0
  foreach ($p in 5760, 5770, 5780, 5790, 5800, 5810, 5820) { if (Test-Port $p) { $open++ } }
  Write-Host ("        puertos listos: {0}/7" -f $open)
  if ($open -eq 7) { $ready = $true; break }
  Start-Sleep -Seconds 3
}
if (-not $ready) {
  Write-Host "  [X] Los contenedores no terminaron de arrancar. Reintenta o revisa Docker Desktop." -ForegroundColor Red
  exit 1
}
Write-Host "        EKF/GPS asentandose (~30 s, para que los drones puedan armar)..."
Start-Sleep -Seconds 30

# 3) arrancar Lhok en modo firmware
Write-Host "  [3/3] Arrancando Lhok (modo firmware). Abri http://127.0.0.1:8010/" -ForegroundColor Green
Write-Host ""
$env:LHOK_BACKEND = "mav"
py run.py
