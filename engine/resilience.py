"""
resilience.py — Capa de coordinación autónoma del enjambre.

Esta capa es lo que correría en el COMPANION COMPUTER de cada dron (junto al
Pixhawk/ArduPilot), no dentro del firmware C++ de vuelo: ArduPilot es de un solo
vehículo y no sabe que hay un enjambre. Acá vive la inteligencia colectiva que
convierte 7 drones en un sistema que se cuida solo. Consume el estado del enjambre
(posiciones, batería, enlace, integridad de GPS) y devuelve, para cada dron, un
OBJETIVO de navegación y un MODO — exactamente lo que a bordo se traduciría en
comandos MAVLink de posición/modo hacia cada autopiloto.

Cuatro conductas autónomas (además del consenso anti-spoofing, que vive en
consensus.py):

  1. INTEGRIDAD GPS DE ENJAMBRE — si la mayoría (≥80%) de los nodos reportan que su
     GPS es inconsistente con lo que miden sus vecinos, el enjambre VOTA apagar el
     GPS en todas las unidades a la vez y pasa a navegar en modo inercial/UWB
     (inmune al spoofing masivo).

  2. RELEVO DE COMUNICACIONES — si un dron pierde el enlace directo con la base
     (sombra de terreno) pero un compañero lo mantiene, ese compañero se vuelve
     PUENTE repetidor. Si TODO el enjambre pierde el enlace (jamming), sube
     escalonado para recuperar línea de vista y, si no, retorna coordinado por el
     último vector limpio.

  3. ENERGÍA COOPERATIVA + RELEVO DE ROLES — los drones comparten su nivel de
     batería; si el líder se está quedando sin energía, el de más batería asume el
     liderazgo y el agotado se repliega al centro (menos viento) o retorna solo.

  4. DISPERSIÓN TÁCTICA — ante iluminación de radar / inhibidor direccional / pérdida
     súbita de varios miembros, el enjambre rompe la formación, evade con maniobras
     aleatorias y se reagrupa en un punto de reunión (rendezvous) memorizado.
"""
import math
import random

from .swarm import BASE, TICK, Z0, slot, leader, _unit

# -- comunicaciones -------------------------------------------------------------
COMMS_RANGE = 380.0       # alcance de enlace directo dron<->base (m)
RELAY_RANGE = 230.0       # alcance de un salto de relevo entre drones (m)
CLIMB_FOR_LOS = 45.0      # ascenso extra para recuperar línea de vista (m)

# -- energía --------------------------------------------------------------------
BATT_DRAIN = 0.65         # consumo base (%/s)
LEADER_EXTRA = 0.55       # el líder/portador de carga gasta más (%/s)
BATT_HANDOFF = 35.0       # umbral para relevar el liderazgo (%)
BATT_RTB = 18.0           # umbral para retorno solitario a base (%)

# -- integridad GPS -------------------------------------------------------------
GPS_KILL_FRAC = 0.8       # fracción de nodos que reportan GPS malo para votar apagarlo

# -- dispersión -----------------------------------------------------------------
SCATTER_TIME = 5.0        # segundos de evasión dispersa
REGROUP_TIME = 9.0        # segundos para converger al rendezvous
RENDEZVOUS_AHEAD = 130.0  # distancia del punto de reunión, adelante del rumbo (m)


def _dist(a, b):
    return math.hypot(a['x'] - b[0], a['y'] - b[1])


def _dd(a, b):
    return math.hypot(a['x'] - b['x'], a['y'] - b['y'])


class Resilience:
    def __init__(self):
        self.leader_id = 0
        # comunicaciones
        self.jamming = False
        self.jam_t = 0.0
        self.blackout_t = 0.0
        self.shadow = set()          # ids sin enlace directo por terreno
        # integridad GPS
        self.gps_killed = False
        # dispersión
        self.scatter = False
        self.scatter_t = 0.0
        self.rendezvous = None

    # ------------------------------------------------------------------ pre-vuelo
    def update(self, world):
        """Corre antes de _fly: fija batería, enlaces, roles, objetivos y modos."""
        active = [d for d in world.drones if not d['down'] and d['status'] != 'captured']
        # por defecto, cada dron navega a su puesto de formación en modo GPS
        for d in active:
            d['goal'] = None
        self._comms(world, active)
        self._energy(world, active)
        self._scatter(world, active)

    # --------------------------------------------------------------- post-consenso
    def post_consensus(self, world):
        """Corre tras el consenso: integridad de GPS a nivel enjambre (voto 80%)."""
        active = [d for d in world.drones if not d['down'] and d['status'] != 'captured']
        if not active:
            return
        # un nodo "reporta GPS inconsistente" si su residual está elevado respecto del
        # ruido nominal (~4 m). Umbral sensible (0.6× el del voto individual): en un
        # spoof aislado sólo lo cruza 1 nodo (no dispara el 80%); en un spoof masivo,
        # casi todos → se vota apagar el GPS del enjambre.
        report_thresh = 0.6 * world.vote_thresh
        reporting = [d for d in active if d['residual'] > report_thresh]
        frac = len(reporting) / len(active)
        if not self.gps_killed and frac >= GPS_KILL_FRAC:
            self.gps_killed = True
            for d in world.drones:
                d['nav_mode'] = 'inertial'
            world.consensus.alert(world, 'high',
                f"VOTO DE ENJAMBRE ({len(reporting)}/{len(active)} ≥ 80%): GPS comprometido en "
                f"masa — se APAGA el GPS del enjambre, todos a navegación inercial/UWB")
        elif self.gps_killed and not reporting:
            self.gps_killed = False
            for d in world.drones:
                d['nav_mode'] = 'gps'
            world.consensus.alert(world, 'info',
                "Integridad de GPS restablecida: el enjambre vuelve a confiar en sus receptores")

    # ------------------------------------------------------------------ conductas
    def _comms(self, world, active):
        for d in active:
            d['gcs_link'] = False
            d['relay_via'] = None
        # enlaces directos con la base (salvo jamming total o sombra de terreno,
        # que se rompe si el dron no ascendió a recuperar línea de vista)
        directs = []
        if not self.jamming:
            for d in active:
                shadowed = d['id'] in self.shadow and d['z'] < Z0 + CLIMB_FOR_LOS - 5
                if _dist(d, BASE) < COMMS_RANGE and not shadowed:
                    d['gcs_link'] = True
                    directs.append(d)
        # relevo: alcanzar por saltos a los que no tienen directo
        connected = {d['id'] for d in directs}
        reached = list(directs)
        changed = True
        while changed:
            changed = False
            for d in active:
                if d['id'] in connected:
                    continue
                for c in reached:
                    if _dd(d, c) < RELAY_RANGE:
                        d['relay_via'] = c['id']
                        d['gcs_link'] = True
                        connected.add(d['id'])
                        reached.append(d)
                        changed = True
                        break
        # los nodos en sombra que ya tienen enlace por relevo: intentan además
        # ascender para recuperar su propio enlace directo (línea de vista)
        for d in active:
            if d['id'] in self.shadow and not any(x['id'] == d['id'] for x in directs):
                gx, gy, _ = d['slot']
                d['goal'] = (gx, gy, Z0 + CLIMB_FOR_LOS)
        # ¿apagón total? (nadie alcanza la base, ni directo ni por relevo)
        if active and not connected:
            self.blackout_t += TICK
            self._blackout(world, active)
        else:
            if self.blackout_t > 0:
                world.consensus.alert(world, 'info',
                    "Enlace con la base recuperado: el enjambre retoma la misión")
            self.blackout_t = 0.0
        # temporizador de jamming
        if self.jamming:
            self.jam_t -= TICK
            if self.jam_t <= 0:
                self.jamming = False
                world.consensus.alert(world, 'info', "Cesa el jamming: el enlace de radio se restablece")

    def _blackout(self, world, active):
        """Sin enlace con la base: primero ascenso escalonado para recuperar línea de
        vista; si no alcanza, retorno coordinado por el último vector limpio."""
        if self.blackout_t < 6.0:
            # ascenso en cadena: cada dron sube un escalón según su id (escalonado)
            for d in active:
                gx, gy, _ = d['slot']
                d['goal'] = (gx, gy, Z0 + 10.0 + 7.0 * d['id'])
            if self.blackout_t < TICK * 2:
                world.consensus.alert(world, 'med',
                    "Enlace con la base perdido (jamming): ascenso escalonado para recuperar línea de vista")
        else:
            # RTL coordinado: todos convergen hacia la base manteniendo separación
            for k, d in enumerate(active):
                ang = 2 * math.pi * k / len(active)
                d['goal'] = (BASE[0] + 30 * math.cos(ang), BASE[1] + 30 * math.sin(ang), Z0)
            if self.blackout_t < 6.0 + TICK * 2:
                world.consensus.alert(world, 'high',
                    "Sin línea de vista: RETORNO COORDINADO a base por el último vector limpio")

    def _energy(self, world, active):
        # consumo: base + extra del líder + variación por viento (según exposición N-S)
        for d in active:
            wind = 1.0 + 0.5 * max(0.0, math.sin(math.radians(d['y'])))
            extra = LEADER_EXTRA if d['role'] == 'leader' else 0.0
            d['battery'] = max(0.0, d['battery'] - (BATT_DRAIN + extra) * wind * TICK)
        healthy = [d for d in active if d['status'] == 'ok' and not d['spoof']]
        leader = next((d for d in active if d['role'] == 'leader'), None)
        # relevo de liderazgo si el líder está bajo de energía
        if leader and leader['battery'] < BATT_HANDOFF:
            cands = [d for d in healthy if d['id'] != leader['id']]
            if cands:
                reliever = max(cands, key=lambda d: d['battery'])
                if reliever['battery'] > leader['battery'] + 12:
                    leader['role'], reliever['role'] = 'follower', 'leader'
                    leader['slot_idx'], reliever['slot_idx'] = reliever['slot_idx'], leader['slot_idx']
                    self.leader_id = reliever['id']
                    world.consensus.alert(world, 'high',
                        f"Relevo de liderazgo: {reliever['name']} ({reliever['battery']:.0f}%) toma el mando; "
                        f"{leader['name']} ({leader['battery']:.0f}%) se repliega al centro")
        # retorno solitario de los agotados
        for d in active:
            if d['battery'] < BATT_RTB and d['role'] != 'rtb':
                d['role'] = 'rtb'
                world.consensus.alert(world, 'med',
                    f"{d['name']}: batería crítica ({d['battery']:.0f}%) — regreso controlado a base")
            if d['role'] == 'rtb':
                d['goal'] = (BASE[0], BASE[1], Z0)

    def _scatter(self, world, active):
        if not self.scatter:
            return
        self.scatter_t += TICK
        if self.scatter_t < SCATTER_TIME:
            # evasión: cada dron acelera en su vector aleatorio memorizado, con zigzag
            for d in active:
                ex, ey = d['evade']
                jitter = 18 * math.sin(self.scatter_t * 3 + d['id'])
                d['goal'] = (d['x'] + ex * 60 + (-ey) * jitter * 0.1,
                             d['y'] + ey * 60 + ex * jitter * 0.1,
                             Z0 + d['evade_z'] * 20)
        elif self.scatter_t < SCATTER_TIME + REGROUP_TIME:
            # converger al rendezvous, cada uno a su puesto relativo
            rx, ry = self.rendezvous
            for d in active:
                bx, by = slot(d['slot_idx'], world.t)[0] - slot(0, world.t)[0], \
                         slot(d['slot_idx'], world.t)[1] - slot(0, world.t)[1]
                d['goal'] = (rx + bx, ry + by, Z0)
        else:
            self.scatter = False
            self.rendezvous = None
            for d in active:
                d['goal'] = None
            world.consensus.alert(world, 'info', "Reagrupamiento completo: formación restablecida en el punto de reunión")

    # ------------------------------------------------------------------ disparadores
    def trigger_jamming(self, world, duration=12.0):
        self.jamming = True
        self.jam_t = duration
        world.consensus.alert(world, 'high',
            "Inhibidor de radio enemigo activo: se corta el enlace de radio con la base")
        return True

    def trigger_comms_shadow(self, world, node):
        d = world._pick(node, [x for x in world.drones if x['status'] != 'captured' and not x['down']])
        if not d:
            return False
        self.shadow.add(d['id'])
        world.consensus.alert(world, 'med',
            f"{d['name']}: entra en sombra de terreno — pierde enlace DIRECTO con la base")
        return True

    def clear_shadow(self):
        self.shadow.clear()

    def trigger_scatter(self, world, reason="radar de defensa aérea"):
        if self.scatter:
            return False
        self.scatter = True
        self.scatter_t = 0.0
        active = [d for d in world.drones if not d['down'] and d['status'] != 'captured']
        # punto de reunión: adelante del centroide, en el sentido del rumbo del líder
        cx = sum(d['x'] for d in active) / len(active)
        cy = sum(d['y'] for d in active) / len(active)
        (lx, ly, _), h = leader(world.t)
        hx, hy = math.cos(h), math.sin(h)
        self.rendezvous = (cx + hx * RENDEZVOUS_AHEAD, cy + hy * RENDEZVOUS_AHEAD)
        for d in active:
            ang = random.uniform(0, 2 * math.pi)
            d['evade'] = (math.cos(ang), math.sin(ang))
            d['evade_z'] = random.choice([-1, 1])
        world.consensus.alert(world, 'high',
            f"Amenaza detectada ({reason}): DISPERSIÓN TÁCTICA — evasión y reunión en punto memorizado")
        return True

    def trigger_kinetic(self, world, n=2):
        """Pérdida súbita de varios miembros (ataque cinético/escopeta antidron)."""
        pool = [d for d in world.drones if not d['down'] and d['status'] != 'captured']
        if len(pool) <= 3:
            return False
        for d in random.sample(pool, min(n, len(pool) - 3)):
            d['down'] = True
            d['down_t'] = 6.0
            d['status'] = 'down'
        world.consensus.alert(world, 'high',
            f"Pérdida súbita de {min(n, len(pool)-3)} nodos (ataque cinético): el enjambre dispersa y se reagrupa")
        self.trigger_scatter(world, reason="pérdida cinética")
        return True
