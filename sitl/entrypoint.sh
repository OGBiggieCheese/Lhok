#!/usr/bin/env bash
# Punto de entrada del contenedor: levanta el enjambre de ArduCopter SITL.
# Cada instancia i expone MAVLink por TCP en 5760+10*i (bind en todas las
# interfaces del contenedor; Docker las publica al host).
set -euo pipefail
cd /ardupilot

N="${FALANGE_N:-7}"
HOME_LOC="${HOME_LOC:--31.4370,-64.1888,470,0}"   # debe coincidir con engine/geo.py

echo "=================================================================="
echo "  FALANGE SITL — levantando $N ArduCopter (firmware real)"
echo "  Home: $HOME_LOC"
echo "=================================================================="

for i in $(seq 0 $((N-1))); do
  port=$((5760 + 10*i))
  sim_vehicle.py -v ArduCopter -I"$i" --sysid $((i+1)) \
      --custom-location="$HOME_LOC" \
      --no-mavproxy --no-rebuild \
      >/tmp/sitl_$i.log 2>&1 &
  echo "  dron $i  ->  tcp:0.0.0.0:$port   (log: /tmp/sitl_$i.log)"
  sleep 3
done

echo "------------------------------------------------------------------"
echo "  Enjambre arriba. Desde el host, arrancá FALANGE en modo firmware:"
echo "    PowerShell:  \$env:FALANGE_BACKEND=\"mav\"; py run.py"
echo "  (o usá Idea1/run-mav.ps1). Ctrl+C para bajar el enjambre."
echo "------------------------------------------------------------------"

# Mantener vivo el contenedor y volcar los logs de todas las instancias.
exec tail -n +1 -F /tmp/sitl_*.log
