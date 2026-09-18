"""
MavWorld — backend de LHOK sobre firmware REAL de ArduPilot (SITL).

Reemplaza al simulador cinemático (`swarm.World`) por N instancias de **ArduPilot
SITL**, que es el firmware de vuelo real (ArduCopter) compilado para correr en la
PC: EKF real, fusión GPS real, navegación real. LHOK deja de "simular drones" y
pasa a **vigilar drones de verdad** que vuelan una formación real.

Por qué esto hace la demo "totalmente real"
--------------------------------------------
Cuando se inyecta spoofing, no se mueve un punto en una simulación: se le mete un
GPS falso al EKF del firmware real. El EKF **se lo cree** y el controlador de vuelo,
para "mantener la formación" según su GPS mentiroso, **arrastra físicamente al dron
fuera de curso**. Su telemetría reporta que sigue en formación mientras su posición
verdadera deriva hacia la trampa. Es el ataque real de guerra electrónica, ejecutado
por el flight controller real.

El ranging entre drones (tipo UWB) se calcula desde la posición **física real** que
SITL conoce (mensaje SIMSTATE), no desde el GPS — igual que un UWB de verdad, que
mide distancia física y no se puede falsificar a distancia.

Contrato con el resto del sistema
---------------------------------
`MavWorld` expone EXACTAMENTE la misma interfaz pública que `swarm.World`
(`config`, `snapshot`, `step`, `inject_spoof`, `trigger_hpm`, `set_defense`,
`set_params`, `set_pause`, `set_speed`, `reset`) y alimenta `consensus.evaluate`
con los mismos dicts de drones y la misma matriz de distancias. Por eso
`consensus.py` y el visor web **no cambian ni una línea**.

Requisitos (ver README, sección "Modo firmware real"):
  - ArduPilot SITL corriendo (N instancias) — sobre Linux/WSL2/Docker.
  - `pip install pymavlink`  (sólo para este backend; el modo demo no lo necesita).

NOTA HONESTA: este backend no se puede ejecutar ni verificar sobre Windows nativo;
necesita el toolchain de SITL en Linux/WSL. El código está diseñado para fallar de
forma ruidosa y clara si SITL no está disponible, y `server.py` cae de vuelta al
simulador puro para que la demo nunca se quede sin funcionar.
"""
import math
import os
import random
import threading
import time

from . import geo
from .consensus import Consensus, VOTE_THRESH_M
from .resilience import Resilience
from .swarm import (
    N, NAMES, TICK, Z0, TRAP, TRAP_R, BASE, SLOTS,
    BIAS_RATE_DEFAULT, SPOOF_DURATION, HPM_DOWN_TIME,
    leader, slot, _unit,
)

RANGE_NOISE = 0.4          # ruido del ranging UWB (m)
TAKEOFF_ALT = Z0           # altura de crucero de la formación (m)
SETPOINT_EVERY = 2         # enviar setpoint cada N ticks (evita saturar el enlace)

# Endpoints MAVLink de cada instancia SITL. Por defecto, el TCP que expone SITL
# (serial0) para la instancia i: 5760 + 10*i. Se puede sobreescribir con la
# variable de entorno LHOK_SITL (lista separada por comas).
def _default_endpoints():
    env = os.environ.get("LHOK_SITL", "").strip()
    if env:
        return [e.strip() for e in env.split(",") if e.strip()]
    return [f"tcp:127.0.0.1:{5760 + 10 * i}" for i in range(N)]


class MavLink:
    """Envoltorio fino sobre una conexión pymavlink a una instancia SITL."""

    def __init__(self, endpoint):
        from pymavlink import mavutil  # import perezoso: sólo si se usa este backend
        self.mavutil = mavutil
        self.endpoint = endpoint
        self.m = mavutil.mavlink_connection(endpoint, source_system=255)
        self.believed = None   # (lat, lon, rel_alt_m) del EKF (GLOBAL_POSITION_INT)
        self.true = None       # (lat, lon) físico real (SIMSTATE)

    def wait_ready(self, timeout=30):
        self.m.wait_heartbeat(timeout=timeout)
        return self.m.target_system != 0

    def request_streams(self, rate_hz=5):
        """Fuerza el envío de telemetría (GLOBAL_POSITION_INT, SIMSTATE, etc.).
        Sin esto, el ritmo por defecto de SITL es poco fiable y las posiciones
        llegan congeladas — el detector no vería la deriva del spoofing."""
        for sid in range(0, 13):
            self.m.mav.request_data_stream_send(
                self.m.target_system, self.m.target_component, sid, rate_hz, 1)

    def clear_spoof(self):
        """Pone a cero cualquier glitch de GPS residual (SITL persiste params en su
        eeprom entre reinicios): así LHOK siempre arranca con el enjambre sano."""
        for base in ("SIM_GPS1_GLITCH_", "SIM_GPS_GLITCH_"):
            for ax in ("X", "Y", "Z"):
                self._param(base + ax, 0.0)

    def wait_armable(self, timeout=60):
        """Espera fix GPS 3D (proxy de que el EKF/GPS ya permiten armar)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            g = self.m.recv_match(type="GPS_RAW_INT", blocking=True, timeout=2)
            if g and g.fix_type >= 3:
                time.sleep(3)   # pequeño asentamiento del EKF tras el fix
                return True
        return False

    def arm_and_takeoff(self, alt, tries=6):
        """Arma confirmando el ACK (reintenta si el EKF aún lo rechaza) y despega."""
        armed = False
        for _ in range(tries):
            self.arm(True)
            ack = self.m.recv_match(type="COMMAND_ACK", blocking=True, timeout=4)
            if ack and ack.command == self.mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM \
                    and ack.result == 0:
                armed = True
                break
            time.sleep(2)
        self.takeoff(alt)
        return armed

    def close(self):
        try:
            self.m.close()
        except Exception:
            pass

    # -- órdenes ----------------------------------------------------------
    def set_mode(self, name):
        self.m.set_mode(self.m.mode_mapping()[name])

    def arm(self, on=True):
        self.m.mav.command_long_send(
            self.m.target_system, self.m.target_component,
            self.mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0,
            1 if on else 0, 0, 0, 0, 0, 0, 0)

    def takeoff(self, alt):
        self.m.mav.command_long_send(
            self.m.target_system, self.m.target_component,
            self.mavutil.mavlink.MAV_CMD_NAV_TAKEOFF, 0, 0, 0, 0, 0, 0, 0, alt)

    def goto(self, lat, lon, rel_alt):
        """Setpoint de posición en GUIDED (frame relativo al home)."""
        mav = self.mavutil.mavlink
        self.m.mav.set_position_target_global_int_send(
            0, self.m.target_system, self.m.target_component,
            mav.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
            0b0000111111111000,               # sólo posición
            int(lat * 1e7), int(lon * 1e7), rel_alt,
            0, 0, 0, 0, 0, 0, 0, 0)

    def set_gps_glitch(self, east_m, north_m, up_m=0.0):
        """Inyecta el spoofing: sesgo (en metros) sumado al GPS simulado de SITL.

        SITL lo aplica al GPS que ve el EKF real -> el EKF se lo cree -> el dron
        deriva físicamente. Se prueban los nombres de parámetro de ArduPilot
        modernos (SIM_GPS1_GLITCH_*) y antiguos (SIM_GPS_GLITCH_*)."""
        dlat = (north_m / geo._R_EARTH) * (180.0 / math.pi)
        dlon = (east_m / (geo._R_EARTH * math.cos(math.radians(geo.HOME_LAT)))) * (180.0 / math.pi)
        for base in ("SIM_GPS1_GLITCH_", "SIM_GPS_GLITCH_"):
            self._param(base + "X", dlat)     # X = offset de latitud (deg)
            self._param(base + "Y", dlon)     # Y = offset de longitud (deg)
            self._param(base + "Z", up_m)     # Z = offset de altitud (m)

    def _param(self, name, value):
        self.m.mav.param_set_send(
            self.m.target_system, self.m.target_component,
            name.encode("ascii"), float(value),
            self.mavutil.mavlink.MAV_PARAM_TYPE_REAL32)

    # -- telemetría -------------------------------------------------------
    def drain(self):
        """Vacía la cola de mensajes y actualiza believed/true."""
        while True:
            msg = self.m.recv_match(blocking=False)
            if msg is None:
                break
            t = msg.get_type()
            if t == "GLOBAL_POSITION_INT":
                self.believed = (msg.lat * 1e-7, msg.lon * 1e-7, msg.relative_alt / 1000.0)
            elif t == "SIMSTATE":
                self.true = (msg.lat * 1e-7, msg.lng * 1e-7)
            elif t == "SIM_STATE":
                self.true = (msg.lat, msg.lon)


class MavWorld:
    """Backend de LHOK respaldado por firmware real de ArduPilot (SITL)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.defense = True
        self.paused = False
        self.speed = 1
        self.bias_rate = BIAS_RATE_DEFAULT
        self.vote_thresh = VOTE_THRESH_M
        self.links = []
        self._tick = 0
        self.reset()

    # -- ciclo de vida ----------------------------------------------------
    def reset(self):
        with self.lock:
            self.t = 0.0
            self._tick = 0
            self.consensus = Consensus(self.vote_thresh)
            self.res = Resilience()
            self.drones = []
            for i in range(N):
                s = slot(i, 0.0)
                self.drones.append({
                    'id': i, 'name': NAMES[i],
                    'x': s[0], 'y': s[1], 'z': s[2],
                    'gx': s[0], 'gy': s[1], 'gz': s[2],
                    'slot': list(s), 'slot_idx': i, 'status': 'ok',
                    'spoof': False, 'bias_dir': (0.0, 0.0), 'bias_mag': 0.0, 'spoof_t': 0.0,
                    'detected': False, 'votes': 0, 'residual': 0.0, 'sustain': 0,
                    'est': None, 'down': False, 'down_t': 0.0, 'armed': True,
                    # capa de resiliencia (misma que el backend sim)
                    'battery': 100.0, 'role': 'leader' if i == 0 else 'follower',
                    'gcs_link': True, 'relay_via': None, 'nav_mode': 'gps',
                    'goal': None, 'evade': (0.0, 0.0), 'evade_z': 0,
                    'true_trail': [], 'gps_trail': [],
                })
            self.ranges = [[0.0] * N for _ in range(N)]
            # al reiniciar el tablero, limpiar cualquier spoofing en el firmware real
            for l in self.links:
                l.clear_spoof()
        if not self.links:
            self.connect()

    def connect(self):
        """Abre el enlace MAVLink con cada instancia SITL, arma y despega en formación."""
        endpoints = _default_endpoints()
        if len(endpoints) < N:
            raise RuntimeError(
                f"LHOK modo firmware: hacen falta {N} instancias SITL, "
                f"hay {len(endpoints)} endpoints. Lanzá el enjambre (ver sitl/launch_swarm.sh) "
                f"o definí LHOK_SITL con {N} endpoints separados por comas.")
        links = []
        for i in range(N):
            # SITL expone un TCP de un solo cliente y tarda un instante en liberar
            # el slot tras una desconexión: reintentamos, y ante fallo cerramos todo.
            link = None
            for _ in range(4):
                try:
                    cand = MavLink(endpoints[i])
                    if cand.wait_ready(timeout=15):
                        cand.request_streams()
                        link = cand
                        break
                    cand.close()
                except Exception:
                    try:
                        cand.close()
                    except Exception:
                        pass
                time.sleep(2.0)
            if link is None:
                for l in links:
                    l.close()
                raise RuntimeError(
                    f"No se pudo conectar al dron {NAMES[i]} en {endpoints[i]} tras varios "
                    f"intentos. ¿Está corriendo el enjambre SITL? (sitl/docker compose up)")
            links.append(link)
        # preparar y despegar: limpiar spoofing residual, esperar EKF/GPS, armar, despegar
        for link in links:
            link.set_mode("GUIDED")
            link.clear_spoof()
        for link in links:
            link.wait_armable(timeout=60)
        for link in links:
            link.arm_and_takeoff(TAKEOFF_ALT)
        time.sleep(2.0)
        self.links = links

    def close(self):
        for l in self.links:
            l.close()
        self.links = []

    # -- comandos (misma firma que World) ---------------------------------
    def _pick(self, node, pool):
        if node is not None:
            for d in pool:
                if d['id'] == node or d['name'] == node:
                    return d
            return None
        return random.choice(pool) if pool else None

    def inject_spoof(self, node=None):
        with self.lock:
            pool = [d for d in self.drones if d['status'] == 'ok' and not d['down'] and not d['spoof']]
            d = self._pick(node, pool)
            if d is None:
                return False
            d['spoof'] = True
            d['bias_mag'] = 0.0
            d['spoof_t'] = 0.0
            d['bias_dir'] = _unit(d['slot'][0] - TRAP[0], d['slot'][1] - TRAP[1])
            self.consensus.alert(self, 'med', f"Atacante: inicia spoofing GPS sobre {d['name']}"
                                              + ("" if self.defense else " (defensa desactivada)"))
            return True

    def trigger_hpm(self, node=None):
        with self.lock:
            pool = [d for d in self.drones if not d['down'] and d['status'] != 'captured']
            if len(pool) <= 3:
                return False
            d = self._pick(node, pool)
            if d is None:
                return False
            d['down'] = True
            d['down_t'] = HPM_DOWN_TIME
            d['spoof'] = False
            d['status'] = 'down'
            d['est'] = None
            d['armed'] = False
            self.links[d['id']].arm(False)  # pulso HPM: se desarma -> cae
            self.consensus.alert(self, 'high', f"{d['name']}: pulso de energía dirigida — enlace perdido, "
                                               f"el enjambre reconfigura la formación")
            return True

    # -- disparadores de la capa de resiliencia (mismos que World; corren sobre
    #    firmware real: dispersión, relevo, ascenso y RTL son comandos MAVLink) --
    def trigger_jamming(self):
        with self.lock:
            return self.res.trigger_jamming(self)

    def trigger_comms_shadow(self, node=None):
        with self.lock:
            return self.res.trigger_comms_shadow(self, node)

    def trigger_scatter(self):
        with self.lock:
            return self.res.trigger_scatter(self)

    def trigger_kinetic(self, n=2):
        with self.lock:
            pool = [d for d in self.drones if not d['down'] and d['status'] != 'captured']
            if len(pool) <= 3:
                return False
            for d in random.sample(pool, min(int(n), len(pool) - 3)):
                d['down'] = True
                d['down_t'] = 6.0
                d['status'] = 'down'
                d['spoof'] = False
                d['est'] = None
                d['armed'] = False
                self.links[d['id']].arm(False)   # se desarma -> cae (real)
            self.consensus.alert(self, 'high',
                "Pérdida súbita de nodos (ataque cinético): el enjambre dispersa y se reagrupa")
            self.res.trigger_scatter(self, reason="pérdida cinética")
            return True

    def trigger_lowbatt(self, node=None):
        with self.lock:
            pool = [d for d in self.drones if d['status'] != 'captured' and not d['down']]
            if node is None:
                d = next((x for x in pool if x['role'] == 'leader'), None)
            else:
                d = self._pick(node, pool)
            if not d:
                return False
            d['battery'] = 30.0
            self.consensus.alert(self, 'med', f"{d['name']}: nivel de batería bajo ({d['battery']:.0f}%)")
            return True

    def set_defense(self, on):
        with self.lock:
            on = bool(on)
            if on == self.defense:
                return
            self.defense = on
            if on:
                self.consensus.alert(self, 'info', "Defensa LHOK activada: los nodos se vigilan mutuamente")
            else:
                self.consensus.alert(self, 'med', "Defensa LHOK desactivada: cada nodo confía ciegamente en su GPS")

    def set_params(self, bias=None, vote=None):
        with self.lock:
            if bias is not None:
                self.bias_rate = max(2.0, min(20.0, float(bias)))
            if vote is not None:
                self.vote_thresh = max(3.0, min(60.0, float(vote)))
                self.consensus.vote_thresh = self.vote_thresh

    def set_pause(self, paused):
        self.paused = bool(paused)

    def set_speed(self, x):
        self.speed = x if x in (1, 2, 8) else 1

    def clock_str(self):
        return f"T+{int(self.t) // 60:02d}:{int(self.t) % 60:02d}"

    # -- avance -----------------------------------------------------------
    def step(self):
        if self.paused:
            return
        with self.lock:
            for _ in range(self.speed):
                self._advance()

    def _advance(self):
        self.t += TICK
        self._tick += 1
        for d in self.drones:
            d['slot'] = list(slot(d['slot_idx'], self.t))

        # 1) leer telemetría real de cada instancia SITL
        for i, (d, link) in enumerate(zip(self.drones, self.links)):
            link.drain()
            if link.believed:
                bx, by, bz = geo.lla_to_enu(link.believed[0], link.believed[1], TAKEOFF_ALT + link.believed[2] - TAKEOFF_ALT)
                d['gx'], d['gy'], d['gz'] = bx, by, max(0.0, link.believed[2])
            if link.true:
                tx, ty, _ = geo.lla_to_enu(link.true[0], link.true[1], 0.0)
                d['x'], d['y'] = tx, ty
                d['z'] = d['gz']  # el spoofing de este escenario es horizontal

        # capa de resiliencia (companion computer): fija goal/batería/enlaces/modo
        self.res.update(self)

        # 2) manejar HPM (rearmado tras el pulso)
        for d in self.drones:
            if d['down']:
                d['down_t'] -= TICK
                if d['down_t'] <= 0:
                    link = self.links[d['id']]
                    link.set_mode("GUIDED")
                    link.arm(True)
                    time.sleep(0.1)
                    link.takeoff(TAKEOFF_ALT)
                    d['down'] = False
                    d['status'] = 'ok'
                    d['armed'] = True
                    self.consensus.alert(self, 'info', f"{d['name']}: reinicio completo, reintegrado a la formación")

        # 3) rampa de spoofing -> inyectar glitch de GPS en el firmware real
        for d in self.drones:
            if not d['spoof']:
                continue
            if self.res.gps_killed:
                # el enjambre votó apagar el GPS: descarta la fuente spoofeada y se realinea
                d['spoof'] = False
                self.links[d['id']].set_gps_glitch(0.0, 0.0)
                continue
            d['spoof_t'] += TICK
            d['bias_mag'] += self.bias_rate * TICK
            d['bias_dir'] = _unit(d['slot'][0] - TRAP[0], d['slot'][1] - TRAP[1])
            bx, by = d['bias_dir']
            self.links[d['id']].set_gps_glitch(bx * d['bias_mag'], by * d['bias_mag'])
            if d['spoof_t'] > SPOOF_DURATION and d['status'] == 'mitigated':
                d['spoof'] = False
                self.links[d['id']].set_gps_glitch(0.0, 0.0)

        # 4) comandar el objetivo por MAVLink: la conducta de resiliencia (dispersión,
        #    RTL, ascenso, regreso), o la posición corregida si LHOK mitiga, o el puesto.
        if self._tick % SETPOINT_EVERY == 0:
            for d, link in zip(self.drones, self.links):
                if d['down'] or d['status'] == 'captured':
                    continue
                if d['goal'] is not None:
                    tgt = d['goal']
                elif self.defense and d['status'] == 'mitigated' and d['est']:
                    tgt = (d['slot'][0] + (d['slot'][0] - d['est'][0]),
                           d['slot'][1] + (d['slot'][1] - d['est'][1]), d['slot'][2])
                else:
                    tgt = d['slot']
                lat, lon, _ = geo.enu_to_lla(tgt[0], tgt[1], 0.0)
                link.goto(lat, lon, tgt[2])

        # 5) ranging físico real (desde posición verdadera) -> matriz UWB
        self._measure()

        # 6) ¿cayó en la trampa?
        for d in self.drones:
            if d['status'] not in ('down', 'captured') and \
               math.hypot(d['x'] - TRAP[0], d['y'] - TRAP[1]) < TRAP_R:
                d['status'] = 'captured'
                d['spoof'] = False
                d['est'] = None
                self.consensus.alert(self, 'high', f"{d['name']}: CAPTURADO — atraído a la zona trampa"
                                                   + ("" if self.defense else " sin defensa activa"))

        # 7) EL NÚCLEO: consenso bizantino (idéntico al modo sim; consensus.py intacto)
        self.consensus.evaluate(self.drones, self.ranges, self, self.defense)
        self.res.post_consensus(self)   # integridad de GPS de enjambre (voto 80%)
        self._trails()

    def _measure(self):
        for i in range(N):
            di = self.drones[i]
            for j in range(N):
                if i == j:
                    self.ranges[i][j] = 0.0
                    continue
                dj = self.drones[j]
                dd = math.sqrt((di['x'] - dj['x']) ** 2 + (di['y'] - dj['y']) ** 2 + (di['z'] - dj['z']) ** 2)
                self.ranges[i][j] = dd + random.uniform(-RANGE_NOISE, RANGE_NOISE)

    def _trails(self):
        for d in self.drones:
            d['true_trail'].append([round(d['x'], 1), round(d['y'], 1), round(d['z'], 1)])
            d['gps_trail'].append([round(d['gx'], 1), round(d['gy'], 1), round(d['gz'], 1)])
            if len(d['true_trail']) > 90:
                d['true_trail'].pop(0)
            if len(d['gps_trail']) > 90:
                d['gps_trail'].pop(0)

    # -- serialización (idéntica a World, para que el visor no cambie) -----
    def config(self):
        path = []
        for k in range(72):
            (lx, ly, lz), _ = leader(float(k))
            path.append([round(lx, 1), round(ly, 1), round(lz, 1)])
        return {
            'z0': Z0, 'extent': 265,
            'trap': {'x': TRAP[0], 'y': TRAP[1], 'z': TRAP[2], 'r': TRAP_R},
            'base': {'x': BASE[0], 'y': BASE[1]},
            'path': path, 'n': N, 'names': NAMES,
            'backend': 'ardupilot-sitl',
        }

    def snapshot(self):
        with self.lock:
            drones = []
            for d in self.drones:
                drones.append({
                    'id': d['id'], 'name': d['name'], 'status': d['status'],
                    'spoof': d['spoof'], 'detected': d['detected'], 'down': d['down'],
                    'votes': d['votes'], 'residual': d['residual'],
                    'true': [round(d['x'], 1), round(d['y'], 1), round(d['z'], 1)],
                    'gps': [round(d['gx'], 1), round(d['gy'], 1), round(d['gz'], 1)],
                    'slot': [round(v, 1) for v in d['slot']],
                    'est': d['est'],
                    'battery': round(d['battery'], 1), 'role': d['role'],
                    'gcs_link': d['gcs_link'], 'relay_via': d['relay_via'],
                    'nav_mode': d['nav_mode'],
                    'true_trail': d['true_trail'], 'gps_trail': d['gps_trail'],
                })
            active = [d for d in self.drones if not d['down'] and d['status'] != 'captured']
            compromised = sum(1 for d in self.drones if d['status'] in ('mitigated', 'down', 'captured'))
            captured = any(d['status'] == 'captured' for d in self.drones)
            under_attack = any(d['spoof'] or d['down'] for d in self.drones)
            mitig = any(d['status'] == 'mitigated' for d in self.drones)
            if captured:
                integrity = 'NODO CAPTURADO'
            elif under_attack and mitig:
                integrity = 'MITIGANDO'
            elif under_attack:
                integrity = 'BAJO ATAQUE'
            else:
                integrity = 'NOMINAL'
            max_votes = max((d['votes'] for d in self.drones), default=0)
            r = self.res
            return {
                'clock': self.clock_str(),
                'defense': self.defense, 'paused': self.paused, 'speed': self.speed,
                'params': {'bias_rate': self.bias_rate, 'vote_thresh': self.vote_thresh},
                'backend': 'ardupilot-sitl',
                'drones': drones,
                'alerts': self.consensus.alerts,
                'res': {
                    'jamming': r.jamming, 'blackout': r.blackout_t > 0,
                    'gps_killed': r.gps_killed, 'scatter': r.scatter,
                    'rendezvous': ([round(r.rendezvous[0], 1), round(r.rendezvous[1], 1)]
                                   if r.rendezvous else None),
                    'leader_id': r.leader_id, 'shadow': sorted(r.shadow),
                },
                'stats': {'active': len(active), 'total': N, 'compromised': compromised,
                          'integrity': integrity, 'votes': max_votes},
            }
