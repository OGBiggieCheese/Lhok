#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# FALANGE — Lanzador del enjambre de firmware REAL (ArduPilot SITL).
#
# Levanta N=7 instancias de ArduCopter (firmware real) sobre el mismo punto de
# origen que usa FALANGE. Cada instancia expone MAVLink por TCP en 5760+10*i,
# que es donde se conecta el backend MavWorld.
#
# Requisitos (una sola vez):
#   - Linux o WSL2 (Ubuntu). En Windows: instalá WSL2 y corré esto adentro.
#   - ArduPilot clonado y compilado para SITL:
#       git clone --recurse-submodules https://github.com/ArduPilot/ardupilot
#       cd ardupilot && Tools/environment_install/install-prereqs-ubuntu.sh -y
#       . ~/.profile
#       ./waf configure --board sitl && ./waf copter
#   - sim_vehicle.py en el PATH (Tools/autotest).
#
# Uso:
#   ./launch_swarm.sh            # primera vez (compila si hace falta)
#   ./launch_swarm.sh --fast     # arranques siguientes (sin recompilar)
#   ./launch_swarm.sh --stop     # baja todo el enjambre
# ---------------------------------------------------------------------------
set -euo pipefail

N=7
HOME_LOC="-31.4370,-64.1888,470,0"   # debe coincidir con engine/geo.py (HOME_*)
VEH="ArduCopter"

if [[ "${1:-}" == "--stop" ]]; then
  echo "Deteniendo el enjambre SITL..."
  pkill -f "sim_vehicle.py" 2>/dev/null || true
  pkill -f "arducopter"     2>/dev/null || true
  echo "Listo."
  exit 0
fi

REBUILD="--rebuild"
[[ "${1:-}" == "--fast" ]] && REBUILD="--no-rebuild"

command -v sim_vehicle.py >/dev/null 2>&1 || {
  echo "ERROR: sim_vehicle.py no está en el PATH."
  echo "Agregá ArduPilot/Tools/autotest al PATH (ver cabecera de este script)."
  exit 1
}

echo "Levantando $N drones ArduCopter (firmware real) en $HOME_LOC ..."
for i in $(seq 0 $((N-1))); do
  # -I$i  -> instancia i, MAVLink TCP en 5760+10*i
  # --no-mavproxy -> SITL expone el TCP directo; MavWorld se conecta ahí
  sim_vehicle.py -v "$VEH" -I"$i" --sysid $((i+1)) \
    --custom-location="$HOME_LOC" \
    --no-mavproxy $REBUILD \
    >/tmp/falange_sitl_$i.log 2>&1 &
  echo "  dron $i -> tcp:127.0.0.1:$((5760 + 10*i))   (log: /tmp/falange_sitl_$i.log)"
  REBUILD="--no-rebuild"   # sólo la primera compila
  sleep 2
done

cat <<EOF

Enjambre arriba. Dejá esta terminal abierta.
Ahora, en Windows (o en la misma máquina), arrancá FALANGE en modo firmware real:

  Windows PowerShell:
    \$env:FALANGE_BACKEND="mav"; python run.py

  Linux/WSL:
    FALANGE_BACKEND=mav python run.py

Si FALANGE corre en Windows y SITL en WSL, exportá los endpoints de WSL:
  \$env:FALANGE_SITL="tcp:127.0.0.1:5760,tcp:127.0.0.1:5770,tcp:127.0.0.1:5780,tcp:127.0.0.1:5790,tcp:127.0.0.1:5800,tcp:127.0.0.1:5810,tcp:127.0.0.1:5820"
(WSL2 reenvía localhost a Windows automáticamente en versiones recientes.)

Para bajar todo:  ./launch_swarm.sh --stop
EOF
