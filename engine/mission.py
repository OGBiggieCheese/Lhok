"""
mission.py — Escenario de misión "Asalto al búnker".

Un enjambre de 7 drones avanza desde su base hacia un búnker enemigo y atraviesa,
en orden, cuatro amenazas reales de guerra electrónica y cinética. Las REACCIONES
del enjambre no están animadas a mano: salen del sistema Lhok.

  1. Zona de JAMMING     -> el enjambre pierde el enlace, asciende para recuperar
                            línea de vista y lo atraviesa.
  2. Zona de SPOOFING    -> a dos drones les mienten el GPS; el MISMO motor de
                            consenso de Lhok (engine/consensus.py) lo detecta,
                            los aísla y reconstruye su posición real.
  3. Dos SOLDADOS enemigos disparan -> el enjambre dispersa con maniobras evasivas
                            y se reagrupa (respuesta cinética de Lhok).
  4. BÚNKER con jamming   -> el primer dron entra sin saberlo y CAE. El enjambre
                            APRENDE que hay jamming en el búnker y ADAPTA el asalto:
                            2 drones hacen maniobras de distracción (erráticas)
                            mientras 2 suben alto, sobrevuelan y caen en PICADO
                            sobre el búnker — el picado es balístico/inercial, no
                            necesita enlace, así que completan la misión igual.

Frontera honesta: Lhok NO decide la misión (eso lo da el comando). Lhok mantiene
al enjambre operativo y reactivo mientras la ejecuta: detecta, corrige, dispersa,
aprende y adapta. La detección de spoofing es LITERALMENTE el consenso de Lhok.
"""
import math
import random
import threading

# reutilizamos el MISMO motor de consenso que el sistema principal Lhok
from .consensus import Consensus, VOTE_THRESH_M

TICK = 0.06
N = 7
NAMES = ["ALFA", "BRAVO-1", "BRAVO-2", "CHARLIE-1", "CHARLIE-2", "DELTA-1", "DELTA-2"]
CRUISE_Z = 60.0
MAX_SPEED = 26.0
ADVANCE_V = 17.0                 # velocidad de avance del enjambre (m/s)
SCALE = 0.42                     # factor para encajar el mapa de misión en el marco del visor

OWN_BASE = (-430.0, 0.0)
BUNKER = (430.0, 0.0)

# Bandas/zonas a lo largo del eje x del avance
JAM1 = (-200.0, -70.0)           # zona de jamming (banda en x)
SPOOF = (70.0, 210.0)            # zona de spoofing (banda en x)
SOLDIERS = [(260.0, -80.0), (260.0, 80.0)]   # dos soldados enemigos
SOLDIER_BAND = (210.0, 300.0)
BUNKER_JAM_R = 95.0              # radio de jamming alrededor del búnker

# Cuña de formación (avanza hacia +x): (atrás en x, costado en y)
SLOTS = [(0, 0), (-32, 22), (-32, -22), (-64, 44), (-64, -44), (-96, 66), (-96, -66)]


def _unit(dx, dy):
    m = math.hypot(dx, dy) or 1.0
    return dx / m, dy / m


class MissionWorld:
    def __init__(self):
        self.lock = threading.Lock()
        self.paused = False
        self.speed = 1
        self.vote_thresh = VOTE_THRESH_M
        self.reset()

    def reset(self):
        with self.lock:
            self.t = 0.0
            self.phase = "APROXIMACION"
            self.lead = [OWN_BASE[0] + 40.0, 0.0]     # punta de la formación
            self.consensus = Consensus(self.vote_thresh)
            self.assault = None                        # sub-estado del asalto
            self.success = False
            self.jam_known = False                     # ¿el enjambre ya sabe del jamming del búnker?
            self.drones = []
            for i in range(N):
                sx = self.lead[0] + SLOTS[i][0]
                sy = self.lead[1] + SLOTS[i][1]
                self.drones.append({
                    'id': i, 'name': NAMES[i],
                    'x': sx, 'y': sy, 'z': CRUISE_Z,
                    'gx': sx, 'gy': sy, 'gz': CRUISE_Z,
                    'status': 'ok', 'role': 'formacion',
                    'spoof': False, 'bias': 0.0, 'bias_dir': (0.0, 0.0),
                    'votes': 0, 'residual': 0.0, 'sustain': 0, 'detected': False, 'est': None,
                    'down': False, 'gcs_link': True, 'nav_mode': 'gps',
                    'goal': None, 'evade': (0.0, 0.0), 'evade_ph': 0.0,
                    'trail': [], 'gtrail': [],
                })
            self.ranges = [[0.0] * N for _ in range(N)]
            self.alerts = []

    def clock_str(self):
        return f"T+{int(self.t) // 60:02d}:{int(self.t) % 60:02d}"

    def alert(self, level, text):
        self.consensus.alert(self, level, text)

    # -- controles --------------------------------------------------------
    def set_pause(self, p):
        self.paused = bool(p)

    def set_speed(self, x):
        self.speed = x if x in (1, 2, 8) else 1

    # -- avance -----------------------------------------------------------
    def step(self):
        if self.paused or self.success:
            return
        with self.lock:
            for _ in range(self.speed):
                self._advance()

    def _advance(self):
        self.t += TICK
        active = [d for d in self.drones if not d['down']]

        # slots de formación relativos a la punta
        for d in self.drones:
            d['slot'] = (self.lead[0] + SLOTS[d['id']][0], self.lead[1] + SLOTS[d['id']][1])

        self._phase_logic(active)

        # navegación: cada dron va hacia su goal (o su puesto)
        for d in self.drones:
            if d['down']:
                d['z'] = max(0.0, d['z'] - 22.0 * TICK)      # cae
                d['gx'], d['gy'], d['gz'] = d['x'], d['y'], d['z']
                d['trail'].append((round(d['x'], 1), round(d['y'], 1), round(d['z'], 1)))
                continue
            goal = d['goal'] if d['goal'] is not None else (d['slot'][0], d['slot'][1], CRUISE_Z)
            # en modo inercial navega por posición verdadera (inmune al spoof)
            ref = (d['x'], d['y'], d['z']) if d['nav_mode'] == 'inertial' else (d['gx'], d['gy'], d['gz'])
            cmd = [1.8 * (goal[k] - ref[k]) for k in range(3)]
            spd = math.sqrt(sum(c * c for c in cmd))
            if spd > MAX_SPEED:
                cmd = [c * MAX_SPEED / spd for c in cmd]
            d['x'] += cmd[0] * TICK
            d['y'] += cmd[1] * TICK
            d['z'] = max(0.0, d['z'] + cmd[2] * TICK)
            # GPS creído: verdadero + sesgo de spoofing (si lo hay)
            if d['spoof']:
                bx, by = d['bias_dir']
                d['gx'] = d['x'] + bx * d['bias']
                d['gy'] = d['y'] + by * d['bias']
            else:
                d['gx'], d['gy'] = d['x'] + random.uniform(-.4, .4), d['y'] + random.uniform(-.4, .4)
            d['gz'] = d['z']
            d['trail'].append((round(d['x'], 1), round(d['y'], 1), round(d['z'], 1)))
            d['gtrail'].append((round(d['gx'], 1), round(d['gy'], 1), round(d['gz'], 1)))
            if len(d['trail']) > 140:
                d['trail'].pop(0)
            if len(d['gtrail']) > 140:
                d['gtrail'].pop(0)

        # ranging físico (UWB) + consenso de Lhok (mismo motor)
        self._measure(active)
        self.consensus.evaluate(self.drones, self.ranges, self, defense=True)

    # -- máquina de fases -------------------------------------------------
    def _phase_logic(self, active):
        lx = self.lead[0]
        # el enjambre avanza mientras no esté frente al búnker (ahí se detiene y asalta)
        if self.phase not in ("APROX_BUNKER", "ASALTO", "EXITO"):
            self.lead[0] += ADVANCE_V * TICK

        # ---- 1) JAMMING ----
        in_jam1 = JAM1[0] <= lx <= JAM1[1]
        if in_jam1 and self.phase == "APROXIMACION":
            self._enter("JAMMING", 'high',
                        "Zona de JAMMING: se corta el enlace de radio con la base")
        if self.phase == "JAMMING":
            for d in active:
                d['gcs_link'] = False
                # respuesta Lhok: ascender para recuperar línea de vista
                d['goal'] = (d['slot'][0], d['slot'][1], CRUISE_Z + 45)
            if lx > JAM1[1]:
                for d in active:
                    d['gcs_link'] = True
                    d['goal'] = None
                self._enter("SPOOFING_WAIT", 'info',
                            "Enlace recuperado por línea de vista: el enjambre sale del jamming")

        # ---- 2) SPOOFING ----
        if self.phase == "SPOOFING_WAIT" and lx >= SPOOF[0]:
            # el atacante spoofea a dos drones del enjambre
            targets = [d for d in active if d['id'] in (3, 5)]
            for d in targets:
                d['spoof'] = True
                d['bias'] = 0.0
                d['bias_dir'] = _unit(random.uniform(-1, 1), random.uniform(-1, 1))
            self._enter("SPOOFING", 'high',
                        "Zona de SPOOFING: le mienten el GPS a CHARLIE-1 y DELTA-1")
        if self.phase == "SPOOFING":
            for d in active:
                if d['spoof']:
                    d['bias'] += 7.0 * TICK
                # cuando Lhok lo aísla, navega por posición reconstruida (inercial/UWB)
                if d['status'] == 'mitigated':
                    d['nav_mode'] = 'inertial'
            if lx > SPOOF[1]:
                for d in active:
                    if d['spoof']:
                        d['spoof'] = False
                        d['bias'] = 0.0
                    d['nav_mode'] = 'gps'
                self._enter("SOLDADOS_WAIT", 'info',
                            "Consenso Lhok: spoofing neutralizado, el enjambre retoma el GPS")

        # ---- 3) SOLDADOS ----
        if self.phase == "SOLDADOS_WAIT" and lx >= SOLDIER_BAND[0]:
            self._enter("SOLDADOS", 'high',
                        "Dos soldados enemigos abren fuego: el enjambre dispersa y evade")
            for d in active:
                ang = random.uniform(0, 2 * math.pi)
                d['evade'] = (math.cos(ang), math.sin(ang))
                d['evade_ph'] = random.uniform(0, 6.28)
        if self.phase == "SOLDADOS":
            for d in active:
                ex, ey = d['evade']
                j = 26 * math.sin(self.t * 4 + d['evade_ph'])
                d['goal'] = (d['slot'][0] + ex * 18 - ey * j * 0.4,
                             d['slot'][1] + ey * 18 + ex * j * 0.4, CRUISE_Z)
            if lx > SOLDIER_BAND[1]:
                for d in active:
                    d['goal'] = None
                self._enter("APROX_BUNKER", 'info',
                            "Fuego enemigo superado: el enjambre se reagrupa y sigue al búnker")

        # ---- 4) BÚNKER: primer dron cae, el enjambre aprende y asalta ----
        if self.phase in ("APROX_BUNKER",) and not self.jam_known:
            # el dron más adelantado entra sin saberlo al jamming del búnker
            vanguard = min(active, key=lambda d: math.hypot(d['x'] - BUNKER[0], d['y'] - BUNKER[1]))
            vanguard['goal'] = (BUNKER[0], BUNKER[1], CRUISE_Z)   # avanza al búnker
            if math.hypot(vanguard['x'] - BUNKER[0], vanguard['y'] - BUNKER[1]) < BUNKER_JAM_R:
                vanguard['down'] = True
                vanguard['status'] = 'lost'
                self.jam_known = True
                self.alert('high',
                           f"{vanguard['name']} entró al jamming del búnker y CAYÓ. "
                           f"El enjambre APRENDE: hay jamming en el objetivo")
                self._plan_assault([d for d in active if d['id'] != vanguard['id']])

        if self.phase == "ASALTO":
            self._assault_logic()

    def _plan_assault(self, survivors):
        survivors = sorted(survivors, key=lambda d: d['id'])
        distract = survivors[:2]
        divers = survivors[2:4]
        overwatch = survivors[4:]
        for d in distract:
            d['role'] = 'distraccion'
        for d in divers:
            d['role'] = 'picado'
        for d in overwatch:
            d['role'] = 'cobertura'
        self.assault = {'t': 0.0, 'distract': [d['id'] for d in distract],
                        'divers': [d['id'] for d in divers], 'over': [d['id'] for d in overwatch]}
        self._enter("ASALTO", 'high',
                    "ASALTO ADAPTADO: 2 drones distraen (errático) y 2 suben y caen en picado "
                    "sobre el búnker (picado inercial: el jamming ya no los detiene)")

    def _assault_logic(self):
        a = self.assault
        a['t'] += TICK
        # drones de distracción: vuelo errático en el borde de la zona de jamming
        for did in a['distract']:
            d = self.drones[did]
            if d['down']:
                continue
            ang = self.t * 1.5 + did
            edge = BUNKER_JAM_R + 55
            d['goal'] = (BUNKER[0] - edge + 30 * math.sin(self.t * 2.3 + did),
                         BUNKER[1] + edge * math.sin(ang) * 0.8 + 40 * math.cos(self.t * 3 + did),
                         CRUISE_Z + 10 * math.sin(self.t * 4 + did))
        # drones de cobertura: se mantienen atrás
        for did in a['over']:
            d = self.drones[did]
            if d['down']:
                continue
            d['goal'] = (BUNKER[0] - 220, BUNKER[1] + (30 if did % 2 else -30), CRUISE_Z)
        # drones de picado: 1) subir alto, 2) sobrevolar el búnker, 3) caer en picado
        for did in a['divers']:
            d = self.drones[did]
            if d['down'] or d['status'] == 'impacto':
                continue
            if a['t'] < 4.0:                      # subir alto
                d['goal'] = (d['x'] + 30, d['y'], CRUISE_Z + 120)
            elif a['t'] < 8.5:                    # sobrevolar el búnker en altura
                d['goal'] = (BUNKER[0], BUNKER[1] + (25 if did % 2 else -25), CRUISE_Z + 130)
            else:                                 # PICADO inercial (no necesita GPS/enlace)
                d['nav_mode'] = 'inertial'
                d['gcs_link'] = False
                d['goal'] = (BUNKER[0], BUNKER[1], 0.0)
                if d['z'] < 8.0:
                    d['status'] = 'impacto'
                    d['down'] = True
                    self.alert('high', f"{d['name']}: IMPACTO en el búnker — objetivo alcanzado")
        # ¿misión cumplida? (los dos de picado impactaron)
        if all(self.drones[i]['status'] == 'impacto' for i in a['divers']):
            self.success = True
            self.phase = "EXITO"
            self.alert('info', "MISIÓN CUMPLIDA: el búnker fue neutralizado pese al jamming")

    def _enter(self, phase, level, text):
        self.phase = phase
        self.alert(level, text)

    def _measure(self, active):
        for i in range(N):
            for j in range(N):
                if i == j or self.drones[i]['down'] or self.drones[j]['down']:
                    self.ranges[i][j] = 0.0
                    continue
                a, b = self.drones[i], self.drones[j]
                dd = math.hypot(a['x'] - b['x'], a['y'] - b['y'])
                self.ranges[i][j] = dd + random.uniform(-0.4, 0.4)

    # -- serialización (mismo marco/esquema que el banco de ensayos: el visor no cambia) --
    def config(self):
        # coordenadas escaladas al marco del visor principal (mismo fondo/cámara)
        return {
            'mission': True, 'extent': 230, 'z0': CRUISE_Z, 'n': N, 'names': NAMES,
            'own_base': {'x': OWN_BASE[0] * SCALE, 'y': OWN_BASE[1] * SCALE},
            'bunker': {'x': BUNKER[0] * SCALE, 'y': BUNKER[1] * SCALE, 'r': BUNKER_JAM_R * SCALE},
            'jam1': {'x0': JAM1[0] * SCALE, 'x1': JAM1[1] * SCALE},
            'spoof': {'x0': SPOOF[0] * SCALE, 'x1': SPOOF[1] * SCALE},
            'soldiers': [{'x': s[0] * SCALE, 'y': s[1] * SCALE} for s in SOLDIERS],
        }

    def snapshot(self):
        with self.lock:
            def sc(p):
                return [round(p[0] * SCALE, 1), round(p[1] * SCALE, 1), round((p[2] if len(p) > 2 else 0) * SCALE, 1)]
            drones = []
            for d in self.drones:
                drones.append({
                    'id': d['id'], 'name': d['name'], 'status': d['status'], 'role': d['role'],
                    'down': d['down'], 'spoof': d['spoof'], 'detected': d['detected'],
                    'gcs_link': d['gcs_link'], 'nav_mode': d['nav_mode'],
                    'votes': d['votes'], 'residual': round(d['residual'], 1),
                    'true': sc((d['x'], d['y'], d['z'])), 'gps': sc((d['gx'], d['gy'], d['gz'])),
                    'slot': sc((d['slot'][0], d['slot'][1], CRUISE_Z)) if 'slot' in d else None,
                    'est': ([round(d['est'][0] * SCALE, 1), round(d['est'][1] * SCALE, 1), round(d['est'][2] * SCALE, 1)] if d['est'] else None),
                    'true_trail': [sc(p) for p in d['trail']], 'gps_trail': [sc(p) for p in d['gtrail']],
                })
            alive = sum(1 for d in self.drones if not d['down'])
            spoofed = any(d['spoof'] for d in self.drones)
            mitig = any(d['status'] == 'mitigated' for d in self.drones)
            integrity = ('NOMINAL' if self.phase in ('APROXIMACION', 'SPOOFING_WAIT', 'SOLDADOS_WAIT') and not spoofed
                         else 'MITIGANDO' if mitig else 'BAJO ATAQUE')
            if self.success:
                integrity = 'MISIÓN CUMPLIDA'
            return {
                'clock': self.clock_str(), 'phase': self.phase, 'paused': self.paused,
                'speed': self.speed, 'success': self.success, 'jam_known': self.jam_known,
                'defense': True, 'params': {'bias_rate': 5, 'vote_thresh': self.vote_thresh},
                'drones': drones, 'alerts': self.consensus.alerts,
                'stats': {'active': alive, 'alive': alive, 'total': N, 'compromised': N - alive,
                          'integrity': integrity, 'votes': max((d['votes'] for d in self.drones), default=0)},
            }
