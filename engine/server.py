"""
Servidor de LHOK — sólo biblioteca estándar de Python (sin dependencias).
Ejecutar:  python -m engine.server     (o)     python run.py
"""
import json
import os
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .swarm import World, TICK

HOST = "127.0.0.1"
PORT = 8010
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")


def _make_world():
    """Selecciona el backend. Por defecto, simulador puro (cero dependencias, siempre
    funciona). Con LHOK_BACKEND=mav usa firmware real de ArduPilot (SITL); si SITL
    no está disponible, avisa y cae de vuelta al simulador para no dejar la demo sin correr."""
    backend = os.environ.get("LHOK_BACKEND", "sim").strip().lower()
    if backend in ("mav", "ardupilot", "sitl", "real"):
        try:
            from .mav_world import MavWorld
            w = MavWorld()
            print("  Backend: FIRMWARE REAL (ArduPilot SITL) — conectado al enjambre.")
            return w
        except Exception as e:
            print("  [!] No se pudo iniciar el backend de firmware real:")
            print(f"      {e}")
            print("  [!] Cayendo al simulador puro (modo demo).")
    return World()


world = _make_world()
mission = None              # escenario de misión (se crea al entrar en ese modo)
mode = "bench"              # "bench" (banco de ensayos) | "mission" (asalto al búnker)


def _active():
    return mission if mode == "mission" else world


CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}


def _sim_loop():
    while True:
        _active().step()
        time.sleep(TICK)


def _to_node(v):
    if v is None:
        return None
    try:
        return int(v)
    except ValueError:
        return v


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path):
        if not os.path.isfile(path):
            return self.send_error(404, "No encontrado")
        ext = os.path.splitext(path)[1].lower()
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            return self._handle_api(path, parse_qs(parsed.query))
        if path in ("/", ""):
            return self._send_file(os.path.join(WEB_DIR, "index.html"))
        rel = os.path.normpath(path.lstrip("/")).replace("\\", "/")
        if rel.startswith(".."):
            return self.send_error(403, "Prohibido")
        return self._send_file(os.path.join(WEB_DIR, rel))

    def _handle_api(self, path, q):
        global mode, mission

        def arg(name, default=None):
            return q.get(name, [default])[0]

        w = _active()

        # -- cambio de modo: banco de ensayos <-> escenario de misión --
        if path == "/api/mode":
            if arg("m", "bench") == "mission":
                from .mission import MissionWorld
                mission = MissionWorld() if mission is None else (mission.reset() or mission)
                mode = "mission"
            else:
                mode = "bench"
            return self._send_json({"ok": True, "mode": mode})
        if path == "/api/config":
            c = _active().config(); c["mode"] = mode
            return self._send_json(c)
        if path == "/api/state":
            s = _active().snapshot(); s["mode"] = mode
            return self._send_json(s)
        # -- controles comunes a los dos modos --
        if path == "/api/pause":
            w.set_pause(arg("on", "1") == "1")
            return self._send_json({"ok": True})
        if path == "/api/speed":
            try:
                w.set_speed(int(arg("x", "1")))
            except ValueError:
                pass
            return self._send_json({"ok": True})
        if path == "/api/reset":
            w.reset()
            return self._send_json({"ok": True})

        # -- ataques del banco de ensayos (no aplican en modo misión: ok:false) --
        def call(method, *a):
            fn = getattr(w, method, None)
            return self._send_json({"ok": bool(fn(*a)) if fn else False, "unsupported": fn is None})
        if path == "/api/spoof":
            return call("inject_spoof", _to_node(arg("node")))
        if path == "/api/hpm":
            return call("trigger_hpm", _to_node(arg("node")))
        if path == "/api/jamming":
            return call("trigger_jamming")
        if path == "/api/shadow":
            return call("trigger_comms_shadow", _to_node(arg("node")))
        if path == "/api/scatter":
            return call("trigger_scatter")
        if path == "/api/kinetic":
            return call("trigger_kinetic", arg("n", "2"))
        if path == "/api/lowbatt":
            return call("trigger_lowbatt", _to_node(arg("node")))
        if path == "/api/defense":
            fn = getattr(w, "set_defense", None)
            if fn:
                fn(arg("on", "1") == "1")
            return self._send_json({"ok": fn is not None})
        if path == "/api/param":
            fn = getattr(w, "set_params", None)
            if fn:
                try:
                    fn(bias=arg("bias"), vote=arg("vote"))
                except ValueError:
                    pass
            return self._send_json({"ok": fn is not None})
        return self.send_error(404, "API desconocida")


def main():
    threading.Thread(target=_sim_loop, daemon=True).start()
    url = f"http://{HOST}:{PORT}/"
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("=" * 60)
    print("  LHOK — Enjambre con navegación por consenso")
    print("  Anti-spoofing GPS distribuido (RAIM de enjambre)")
    print("=" * 60)
    print(f"  Tablero:  {url}")
    print("  (Ctrl+C para detener)")
    print("=" * 60)
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDetenido.")
        server.shutdown()


if __name__ == "__main__":
    main()
