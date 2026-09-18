"""
bench.py — Banco de métricas determinista de Lhok.

Corre EXACTAMENTE el mismo ataque de spoofing GPS con la defensa de Lhok ACTIVA y
DESACTIVADA, y mide el resultado. Genera los números que se muestran en el pitch
(desvío de ruta con/sin defensa, latencia de detección, captura, falsos positivos).

Uso:  py bench.py       (imprime la tabla y guarda web/metrics.json)
"""
import json
import math
import os
import random

from engine.swarm import World, TICK, TRAP, TRAP_R

NODE = 3            # dron atacado (CHARLIE-1)
ATTACK_AT = 3.0    # segundos hasta iniciar el spoofing
DURATION = 65.0    # segundos de simulación
MARKS = (10, 30, 60)


def _dev(d):
    """Desvío de ruta: distancia entre la posición REAL del dron y su puesto en la formación."""
    return math.hypot(d['x'] - d['slot'][0], d['y'] - d['slot'][1])


def run(defense, seed=1234):
    random.seed(seed)
    w = World()
    w.defense = defense
    injected = False
    inj_t = 0.0
    detect_t = None
    dev = {}
    max_dev = 0.0
    captured = False
    steps = int(DURATION / TICK)
    for _ in range(steps):
        w.step()
        t = w.t
        if not injected and t >= ATTACK_AT:
            w.inject_spoof(NODE)
            injected = True
            inj_t = t
        if not injected:
            continue
        d = w.drones[NODE]
        el = t - inj_t
        if detect_t is None and d['status'] == 'mitigated':
            detect_t = el
        cur = _dev(d)
        max_dev = max(max_dev, cur)
        for m in MARKS:
            if m not in dev and el >= m:
                dev[m] = round(cur, 1)
        if d['status'] == 'captured':
            captured = True
    return {
        'defense': defense,
        'dev': {m: dev.get(m) for m in MARKS},
        'max_dev': round(max_dev, 1),
        'detect_s': round(detect_t, 1) if detect_t is not None else None,
        'captured': captured,
    }


def false_positives(seed=1234):
    """Corre 60 s SIN ningún ataque y cuenta cuántos nodos sanos se marcan por error."""
    random.seed(seed)
    w = World()
    flagged = set()
    for _ in range(int(60 / TICK)):
        w.step()
        for d in w.drones:
            if d['status'] == 'mitigated':
                flagged.add(d['id'])
    return len(flagged)


def main():
    on = run(True)
    off = run(False)
    fp = false_positives()

    print("=" * 64)
    print("  LHOK — BANCO DE MÉTRICAS (spoofing GPS sobre CHARLIE-1)")
    print("=" * 64)
    print(f"  {'Desvío de ruta':<22}{'10 s':>9}{'30 s':>9}{'60 s':>9}   máx")
    print(f"  {'SIN defensa':<22}"
          + "".join(f"{_fmt(off['dev'][m]):>9}" for m in MARKS)
          + f"   {off['max_dev']} m")
    print(f"  {'CON defensa (Lhok)':<22}"
          + "".join(f"{_fmt(on['dev'][m]):>9}" for m in MARKS)
          + f"   {on['max_dev']} m")
    print("-" * 64)
    print(f"  Latencia de detección (Lhok):  {on['detect_s']} s")
    print(f"  Resultado SIN defensa:         {'CAPTURADO en la trampa' if off['captured'] else 'no capturado'}")
    print(f"  Resultado CON defensa:         {'CAPTURADO' if on['captured'] else 'neutralizado, vuelve a formación'}")
    print(f"  Falsos positivos (60 s sanos): {fp}")
    print("=" * 64)

    out = {
        'attack': 'GPS spoofing', 'node': 'CHARLIE-1', 'marks_s': list(MARKS),
        'without': off, 'with': on, 'false_positives': fp,
    }
    path = os.path.join(os.path.dirname(__file__), 'web', 'metrics.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"  métricas guardadas en {path}")


def _fmt(v):
    return f"{v} m" if v is not None else "—"


if __name__ == "__main__":
    main()
