"""
Motor de consenso anti-spoofing.

Idea central: un dron solo no puede saber si su GPS le miente. Pero un enjambre sí,
porque los drones se MIDEN entre sí por radio (ranging tipo UWB), y esa distancia física
es imposible de falsificar a distancia. Es un "RAIM distribuido": en vez de cruzar
satélites, se cruzan vecinos.

Detección:
  Para el dron i, se compara la distancia que IMPLICAN las posiciones GPS reportadas
  |g_i - g_j| contra la distancia realmente MEDIDA por radio r_ij. Si el GPS de i está
  spoofeado, g_i es falso y ese desacuerdo aparece contra MUCHOS vecinos a la vez.
  Cada vecino en desacuerdo emite un "voto". Si la mayoría vota => i está comprometido
  (tolerancia bizantina: alcanza con que la mayoría del enjambre esté sana).

Corrección:
  La posición real del dron spoofeado se reconstruye por multilateración a partir de las
  distancias medidas a los vecinos sanos, y se reinyecta para que vuelva a la formación.

Con la defensa DESACTIVADA el motor sigue calculando votos y residuales (para mostrar lo
que "vería"), pero no aísla ni corrige: el dron confía ciegamente en su GPS.
"""
import math

VOTE_THRESH_M = 12.0      # desacuerdo (m) para que un vecino emita un voto
MAJORITY_FRAC = 0.5       # fracción de vecinos necesaria para marcar comprometido
SUSTAIN_TICKS = 20        # ticks sostenidos (~1,25 s) para confirmar (anti-falsos positivos)


def _dist(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


class Consensus:
    def __init__(self, vote_thresh=VOTE_THRESH_M):
        self.vote_thresh = vote_thresh
        self.alerts = []

    def alert(self, world, level, text):
        self.alerts.insert(0, {'t': world.clock_str(), 'level': level, 'text': text})
        if len(self.alerts) > 80:
            self.alerts.pop()

    def evaluate(self, drones, ranges, world, defense=True):
        active = [i for i, d in enumerate(drones) if not d['down'] and d['status'] != 'captured']
        for i in active:
            di = drones[i]
            gi = (di['gx'], di['gy'], di['gz'])
            residuals = []
            votes = 0
            for j in active:
                if j == i:
                    continue
                dj = drones[j]
                implied = _dist(gi, (dj['gx'], dj['gy'], dj['gz']))
                diff = abs(implied - ranges[i][j])
                residuals.append(diff)
                if diff > self.vote_thresh:
                    votes += 1

            residuals.sort()
            med = residuals[len(residuals) // 2] if residuals else 0.0
            di['votes'] = votes
            di['residual'] = round(med, 1)

            need = int((len(active) - 1) * MAJORITY_FRAC) + 1
            flagged = votes >= need
            di['sustain'] = min(SUSTAIN_TICKS + 2, di['sustain'] + 1) if flagged else max(0, di['sustain'] - 1)

            if not defense:
                # sin defensa: se observa pero no se actúa
                if di['status'] == 'mitigated':
                    di['status'] = 'ok'
                    di['detected'] = False
                    di['est'] = None
                continue

            if di['sustain'] >= SUSTAIN_TICKS and di['status'] != 'mitigated':
                di['status'] = 'mitigated'
                di['detected'] = True
                self.alert(world, 'high',
                           f"{di['name']}: spoofing GPS confirmado por consenso "
                           f"({votes} de {len(active) - 1} vecinos) — nodo aislado, posición reconstruida")

            if di['status'] == 'mitigated':
                est = self._multilaterate(i, drones, ranges, active)
                di['est'] = [round(est[0], 2), round(est[1], 2), round(est[2], 2)]

            if di['status'] == 'mitigated' and not di['spoof'] and di['sustain'] == 0:
                di['status'] = 'ok'
                di['detected'] = False
                di['est'] = None
                self.alert(world, 'info', f"{di['name']}: integridad GPS restablecida, vuelve a confiar en su receptor")

    def _multilaterate(self, i, drones, ranges, active):
        """Estima la posición real del dron i usando las distancias a los vecinos sanos."""
        anchors = []
        for j in active:
            if j == i or drones[j]['status'] == 'mitigated':
                continue
            dj = drones[j]
            anchors.append((j, (dj['gx'], dj['gy'], dj['gz'])))
        if not anchors:
            return [drones[i]['gx'], drones[i]['gy'], drones[i]['gz']]

        p = [sum(a[1][k] for a in anchors) / len(anchors) for k in range(3)]
        for _ in range(80):
            gx = gy = gz = 0.0
            for (j, apos) in anchors:
                dv = (p[0] - apos[0], p[1] - apos[1], p[2] - apos[2])
                dd = math.sqrt(dv[0] ** 2 + dv[1] ** 2 + dv[2] ** 2) + 1e-6
                err = dd - ranges[i][j]
                gx += err * dv[0] / dd
                gy += err * dv[1] / dd
                gz += err * dv[2] / dd
            p[0] -= 0.12 * gx
            p[1] -= 0.12 * gy
            p[2] -= 0.12 * gz
        return p
