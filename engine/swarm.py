"""
Simulador del enjambre de drones en formación.

Cada dron:
  - Vuela una misión en formación (un circuito) siguiendo su posición GPS "creída".
  - Mide distancias por radio a los demás (ranging físico, no falsificable).

Ataques:
  - SPOOFING GPS: el atacante inyecta un sesgo creciente en el GPS de un dron para
    desviarlo físicamente hacia una "zona trampa". El dron cree que sigue en formación
    (rastro AZUL sobre la ruta) mientras físicamente (rastro VERDE) es arrastrado a la
    trampa. Con la defensa activa, el consenso lo detecta, reinyecta su posición real y
    VUELVE solo. Sin defensa, termina CAPTURADO en la trampa.
  - ENERGÍA DIRIGIDA (HPM, tipo Leonidas): un pulso "apaga" un dron temporalmente. El
    enjambre detecta la pérdida, reconfigura, y el nodo se reintegra al reiniciarse.

El detector NO sabe qué dron está atacado: sólo recibe posiciones GPS y distancias medidas.
"""
import math
import random
import threading

from .consensus import Consensus, VOTE_THRESH_M

TICK = 0.06                 # segundos reales por paso (tiempo real)
N = 7
Z0 = 60.0
R_LOOP = 110.0
OMEGA = 2 * math.pi / 72.0
NAV_GAIN = 1.5
GPS_NOISE = 0.5
RANGE_NOISE = 0.4
BIAS_RATE_DEFAULT = 5.0    # velocidad de arrastre del spoofing (m/s de sesgo acumulado)
MAX_SPEED = 11.0           # velocidad física máxima de un dron (m/s)
SPOOF_DURATION = 18.0
# La trampa queda fuera del barrido de la formación (radio máx. ~160 m desde el origen).
TRAP = (215.0, -60.0, 0.0)
TRAP_R = 34.0
BASE = (-150.0, 130.0)
HPM_DOWN_TIME = 4.5

SLOTS = [(0, 0), (-24, 17), (-24, -17), (-48, 34), (-48, -34), (-70, 50), (-70, -50)]
NAMES = ["ALFA", "BRAVO-1", "BRAVO-2", "CHARLIE-1", "CHARLIE-2", "DELTA-1", "DELTA-2"]


def _unit(vx, vy):
    m = math.hypot(vx, vy) or 1.0
    return vx / m, vy / m


# -- geometría de formación (compartida por ambos backends: sim puro y MAVLink) --
# El líder recorre un circuito circular; cada dron mantiene su puesto en la cuña,
# expresado en el marco local (x=Este, y=Norte, z=arriba), en metros.

def leader(t):
    a = OMEGA * t
    return (R_LOOP * math.cos(a), R_LOOP * math.sin(a), Z0), a + math.pi / 2


def slot(i, t):
    (lx, ly, lz), h = leader(t)
    fx, fy = math.cos(h), math.sin(h)
    sx, sy = -math.sin(h), math.cos(h)
    back, side = SLOTS[i]
    return (lx + back * fx + side * sx, ly + back * fy + side * sy, lz + (i % 2) * 3.0)


class World:
    def __init__(self):
        self.lock = threading.Lock()
        self.defense = True
        self.paused = False
        self.speed = 1
        self.bias_rate = BIAS_RATE_DEFAULT
        self.vote_thresh = VOTE_THRESH_M
        self.reset()

    def reset(self):
        from .resilience import Resilience  # import perezoso: evita el ciclo swarm<->resilience
        with self.lock:
            self.t = 0.0
            self.consensus = Consensus(self.vote_thresh)
            self.res = Resilience()
            self.drones = []
            for i in range(N):
                s = self._slot(i, 0.0)
                self.drones.append({
                    'id': i, 'name': NAMES[i],
                    'x': s[0], 'y': s[1], 'z': s[2],
                    'gx': s[0], 'gy': s[1], 'gz': s[2],
                    'slot': list(s), 'slot_idx': i, 'status': 'ok',
                    'spoof': False, 'bias_dir': (0.0, 0.0), 'bias_mag': 0.0, 'spoof_t': 0.0,
                    'detected': False, 'votes': 0, 'residual': 0.0, 'sustain': 0,
                    'est': None, 'down': False, 'down_t': 0.0,
                    # capa de resiliencia
                    'battery': 100.0, 'role': 'leader' if i == 0 else 'follower',
                    'gcs_link': True, 'relay_via': None, 'nav_mode': 'gps',
                    'goal': None, 'evade': (0.0, 0.0), 'evade_z': 0,
                    'true_trail': [], 'gps_trail': [],
                })
            self.ranges = [[0.0] * N for _ in range(N)]

    # -- geometría (delegada a las funciones de módulo, compartidas con MavWorld) --
    def _leader(self, t):
        return leader(t)

    def _slot(self, i, t):
        return slot(i, t)

    def clock_str(self):
        return f"T+{int(self.t) // 60:02d}:{int(self.t) % 60:02d}"

    def _pick(self, node, pool):
        if node is not None:
            for d in pool:
                if d['id'] == node or d['name'] == node:
                    return d
            return None
        return random.choice(pool) if pool else None

    # -- comandos -------------------------------------------------------------
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
            self.consensus.alert(self, 'high', f"{d['name']}: pulso de energía dirigida — enlace perdido, "
                                               f"el enjambre reconfigura la formación")
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

    # -- disparadores de la capa de resiliencia -------------------------------
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
            return self.res.trigger_kinetic(self, int(n))

    def trigger_lowbatt(self, node=None):
        """Fuerza batería baja en un nodo (por defecto el líder) para demostrar el
        relevo de liderazgo sin esperar el consumo natural."""
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

    def set_pause(self, paused):
        self.paused = bool(paused)

    def set_speed(self, x):
        self.speed = x if x in (1, 2, 4) else 1

    # -- avance ---------------------------------------------------------------
    def step(self):
        if self.paused:
            return
        with self.lock:
            for _ in range(self.speed):
                self._advance()

    def _advance(self):
        self.t += TICK
        for d in self.drones:
            d['slot'] = list(self._slot(d['slot_idx'], self.t))
        self.res.update(self)          # comms, energía, dispersión → fija goal/modo
        self._fly()
        self._measure()
        self.consensus.evaluate(self.drones, self.ranges, self, self.defense)
        self.res.post_consensus(self)  # integridad GPS de enjambre (voto 80%)
        self._trails()

    def _fly(self):
        for d in self.drones:
            if d['down']:
                d['down_t'] -= TICK
                d['z'] = max(6.0, d['z'] - 9.0 * TICK)
                if d['down_t'] <= 0:
                    s = d['slot']
                    d['down'] = False
                    d['status'] = 'ok'
                    d['x'], d['y'], d['z'] = s[0], s[1], s[2]
                    d['gx'], d['gy'], d['gz'] = s[0], s[1], s[2]
                    self.consensus.alert(self, 'info', f"{d['name']}: reinicio completo, reintegrado a la formación")
                continue

            if d['status'] == 'captured':
                d['z'] = max(2.0, d['z'] - 6.0 * TICK)
                d['gx'], d['gy'], d['gz'] = d['x'], d['y'], d['z']
                continue

            if d['spoof']:
                # el atacante sigue "tirando" del dron hacia la trampa mientras dure el ataque
                d['spoof_t'] += TICK
                d['bias_mag'] += self.bias_rate * TICK
                d['bias_dir'] = _unit(d['slot'][0] - TRAP[0], d['slot'][1] - TRAP[1])
                # el atacante desiste sólo si el consenso lo neutralizó; sin defensa persiste
                if d['spoof_t'] > SPOOF_DURATION and d['status'] == 'mitigated':
                    d['spoof'] = False

            # objetivo: el que fije la capa de resiliencia (dispersión, RTL, ascenso,
            # regreso), o el puesto de formación si no hay conducta activa
            goal = d['goal'] if d['goal'] else d['slot']
            # posición "creída": en inercial se navega por posición verdadera (UWB),
            # inmune al spoofing; si el nodo está mitigado, por la reconstruida
            if d['nav_mode'] == 'inertial':
                bel = (d['x'], d['y'], d['z'])
            elif d['status'] == 'mitigated' and d['est']:
                bel = d['est']
            else:
                bel = (d['gx'], d['gy'], d['gz'])
            # control proporcional hacia el objetivo, con velocidad física acotada
            cmd = [NAV_GAIN * (goal[k] - bel[k]) for k in range(3)]
            spd = math.sqrt(cmd[0] ** 2 + cmd[1] ** 2 + cmd[2] ** 2)
            if spd > MAX_SPEED:
                cmd = [c * MAX_SPEED / spd for c in cmd]
            for k, ax in enumerate(('x', 'y', 'z')):
                d[ax] += cmd[k] * TICK + random.uniform(-0.04, 0.04)

            if d['spoof']:
                bx, by = d['bias_dir']
                d['gx'] = d['x'] + bx * d['bias_mag'] + random.uniform(-GPS_NOISE, GPS_NOISE)
                d['gy'] = d['y'] + by * d['bias_mag'] + random.uniform(-GPS_NOISE, GPS_NOISE)
            else:
                d['gx'] = d['x'] + random.uniform(-GPS_NOISE, GPS_NOISE)
                d['gy'] = d['y'] + random.uniform(-GPS_NOISE, GPS_NOISE)
            d['gz'] = d['z'] + random.uniform(-GPS_NOISE, GPS_NOISE)

            # ¿cayó en la trampa?
            if math.hypot(d['x'] - TRAP[0], d['y'] - TRAP[1]) < TRAP_R:
                d['status'] = 'captured'
                d['spoof'] = False
                d['est'] = None
                self.consensus.alert(self, 'high', f"{d['name']}: CAPTURADO — atraído a la zona trampa"
                                                   + ("" if self.defense else " sin defensa activa"))

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

    # -- serialización --------------------------------------------------------
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
                'drones': drones,
                'alerts': self.consensus.alerts,
                'res': {
                    'jamming': r.jamming, 'blackout': r.blackout_t > 0,
                    'gps_killed': r.gps_killed, 'scatter': r.scatter,
                    'rendezvous': ([round(r.rendezvous[0], 1), round(r.rendezvous[1], 1)]
                                   if r.rendezvous else None),
                    'leader_id': r.leader_id,
                    'shadow': sorted(r.shadow),
                },
                'stats': {'active': len(active), 'total': N, 'compromised': compromised,
                          'integrity': integrity, 'votes': max_votes},
            }
