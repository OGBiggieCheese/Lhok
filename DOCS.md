# LHOK — El enjambre que se cuida solo

**Navegación por consenso: detección y corrección de spoofing GPS distribuida en un enjambre de drones.**
Hackathon Nacional de Ciberdefensa CYBER.AR 2026 — Eje 1 (sistemas autónomos y no tripulados).

---

## El problema

Los enjambres de drones son el futuro del combate, y su talón de Aquiles es la guerra
electrónica. Con **spoofing de GPS**, un atacante puede inyectar señales falsas para
"secuestrar" un dron: lo desvía físicamente hacia una zona trampa mientras el dron cree
que sigue en curso. Un dron **solo** no puede saber si su GPS le miente.

## La idea

Los aviones tienen **RAIM** (chequean si el GPS es consistente); un dron chico no puede
llevarlo solo. **LHOK lo lleva al enjambre**: los drones se **miden entre sí por radio**
(ranging tipo UWB), y esa distancia física **no se puede falsificar a distancia**.

- Si el GPS de un dron es coherente con las distancias que sus vecinos le miden → sano.
- Si un dron reporta una posición GPS que **contradice** lo que mide todo el enjambre →
  está spoofeado. Los vecinos **votan** (tolerancia bizantina: alcanza con que la mayoría
  esté sana), lo **aíslan**, y **reconstruyen su posición real** por multilateración para
  reinyectarla. El dron **vuelve solo** a la formación.

> En una frase: *"No confían en el satélite. Confían entre ellos."*

## Cómo se ejecuta

Requisito: Python 3.8+. **No hay que instalar nada.**

```bash
python run.py
```

(En Windows también doble clic en `run.bat`.) Abre `http://127.0.0.1:8010/`.

> Corre en el puerto **8010**, así que podés tener ATALAYA (8000) y LHOK a la vez.

## La demostración (para los 3 minutos)

El visor es un **banco de ensayos**: vista en planta de la misión (norte arriba), el enjambre de
7 drones en cuña, la **malla de ranging** (líneas grises) y la **zona trampa** naranja. Una franja
superior **narra en lenguaje llano** qué está pasando en cada momento (6 fases).

**Lo más simple: apretá `▶ Demostración guiada` (o la tecla `G`).** En ~70 s corre sola:

1. *Formación nominal* — cada dron mide por radio la distancia a sus vecinos.
2. *Ataque con la defensa activa* — un dron **cree seguir en formación** (cuadrado azul, "el GPS
   dice que está acá") mientras físicamente es arrastrado hacia la trampa (flecha naranja).
   En los enlaces aparecen las marcas **✗** de cada vecino que lo contradice; cuando la mayoría
   vota, el nodo queda **aislado**, su posición real se reconstruye (marca ⊕) y **vuelve solo**.
3. *El mismo ataque, sin defensa* — se apaga el interruptor **Defensa LHOK**: nadie contradice
   al GPS, y el dron termina **CAPTURADO** en la trampa. Es el contrafáctico: lo que le pasa
   hoy a un dron aislado.

Controles manuales (para las preguntas del jurado):

- **Spoofing GPS** (`A`) / **Pulso HPM** (`H`) sobre el nodo seleccionado (clic en el visor, en la
  tabla, o teclas `1`–`7`), o aleatorio si no hay selección.
- **Interruptor Defensa LHOK** — con/sin consenso, en vivo.
- **Velocidad de arrastre del spoofing** y **umbral de voto** — para mostrar la robustez del
  método ("¿y si el atacante es más sutil?", "¿y si bajamos el umbral?").
- **Residual de consenso** — strip chart de los últimos 30 s con la línea de umbral: se ve cómo
  el residual del nodo atacado se dispara y el de los sanos no.
- Vistas **Planta / Perspectiva / Seguir nodo**, capas, pausa (`espacio`) y velocidad ×1/×2/×4.

## Capa de resiliencia autónoma (companion computer)

Además del consenso anti-spoofing, LHOK incluye una **capa de coordinación de enjambre**
(`resilience.py`) — la inteligencia colectiva que correría en el *companion computer* de cada
dron, no en el C++ de ArduPilot (que es de un solo vehículo). Consume el estado del enjambre y
devuelve, por dron, un objetivo de navegación y un modo (traducibles a comandos MAVLink):

1. **Integridad de GPS de enjambre** — si ≥80% de los nodos reportan GPS inconsistente con lo
   que miden sus vecinos, el enjambre **vota apagar el GPS** de todas las unidades y pasa a
   navegación **inercial/UWB** (inmune al spoofing masivo).
2. **Relevo de comunicaciones** — si un dron pierde el enlace directo con la base (sombra de
   terreno) pero un compañero lo mantiene, ese compañero se vuelve **puente repetidor**. Si
   todo el enjambre pierde el enlace (**jamming**), sube escalonado para recuperar línea de
   vista y, si no, **retorna coordinado** por el último vector limpio.
3. **Energía cooperativa + relevo de roles** — los drones comparten batería; si el líder se
   queda sin energía, el de más batería **asume el mando** y el agotado se repliega o retorna.
4. **Dispersión táctica** — ante radar/inhibidor/pérdida cinética, el enjambre **dispersa** con
   evasión y se **reagrupa** en un punto de reunión memorizado (*rendezvous*).

Cada una tiene su **escenario guiado** en el visor (botones "Escenarios guiados"). Estos ataques
externos (radar, jamming, viento) SITL no los provee, así que se modelan en esta capa — igual que
a bordo. El backend de firmware real (`mav`) implementa hoy el núcleo anti-spoofing; la capa de
resiliencia corre en el backend `sim`.

## Arquitectura

```
engine/
  swarm.py      Simulación cinemática del enjambre + ataques (spoofing / HPM)  [backend "sim"]
  resilience.py Capa de coordinación autónoma: integridad GPS, comms, energía, dispersión
  mav_world.py  Enjambre de FIRMWARE REAL de ArduPilot (SITL) vía MAVLink       [backend "mav"]
  geo.py        Conversión marco local <-> lat/lon para hablar con ArduPilot
  consensus.py  Detección por votación bizantina + multilateración (el núcleo)
  server.py     Servidor HTTP (biblioteca estándar) + API REST + visor + selector de backend
web/
  index.html · style.css · app.js   Visor 3D en canvas puro (sin Three.js ni librerías)
sitl/
  launch_swarm.sh   Lanzador de las N instancias ArduPilot SITL (Linux/WSL)
run.py / run.bat  Lanzadores
```

**Clave de diseño:** `consensus.py` **no sabe** qué dron está atacado. Sólo recibe las
posiciones GPS reportadas y la matriz de distancias medidas. Por eso el mismo motor de
consenso corre, sin cambiar una línea, contra el simulador cinemático **o** contra el
firmware real. `MavWorld` expone la misma interfaz que `World`; el visor tampoco cambia.

## Modo firmware real (ArduPilot SITL)

Además del simulador de demo (por defecto, cero dependencias), LHOK puede vigilar
**drones de verdad**: N instancias de **ArduPilot SITL**, que es el firmware de vuelo real
(ArduCopter) corriendo en la PC, con su EKF real, su fusión GPS real y su navegación real.

**Por qué esto vuelve la simulación totalmente real.** El spoofing ya no mueve un punto:
se le inyecta un GPS falso al EKF del firmware real (glitch de GPS de SITL). El EKF **se lo
cree** y el controlador, para "mantener la formación" según su GPS mentiroso, **arrastra
físicamente al dron** hacia la trampa, mientras su telemetría reporta que sigue en curso.
Es el ataque real de guerra electrónica ejecutado por el flight controller real. El ranging
(UWB) se calcula desde la posición **física verdadera** de SITL — imposible de falsificar a
distancia, como el UWB real.

**Cómo se corre — con Docker (recomendado en Windows; no hay que instalar ArduPilot):**

1. Construí y levantá el enjambre de firmware real (la imagen trae ArduCopter compilado):
   ```bash
   cd Idea1/sitl
   docker compose build     # primera vez: compila el firmware (~15-30 min)
   docker compose up        # levanta los 7 ArduCopter
   ```
2. En otra terminal, arrancá LHOK en modo firmware real:
   ```bash
   pip install -r requirements-mav.txt      # una vez (pymavlink)
   ../run-mav.ps1                            # PowerShell  (o run-mav.bat)
   ```
El visor en `http://127.0.0.1:8010/` es idéntico; ahora los 7 puntos son 7 ArduCopter reales.
Los contenedores exponen `tcp:127.0.0.1:5760,5770,…,5820`, que es lo que LHOK busca por defecto.

**Alternativa sin Docker** (WSL2/Linux con ArduPilot ya compilado):
```bash
Idea1/sitl/launch_swarm.sh --fast        # levanta el enjambre nativo
$env:LHOK_BACKEND="mav"; py run.py     # arranca LHOK
```

> **Robustez de demo:** si SITL no está corriendo (o falta `pymavlink`), el servidor avisa
> y **cae solo al simulador puro**. La demo nunca se queda sin funcionar. Endpoints de las
> instancias configurables con `LHOK_SITL`.

*Comportamiento real del firmware:* al inyectar spoofing, el EKF real **resiste** el GPS
falso (gating de innovación) y cede por saltos hasta que el dron es arrastrado físicamente
—más realista que un simulador—; el consenso lo detecta y lo aísla igual.

**Operación / troubleshooting (probado en Windows + Docker):**
- Arrancá el enjambre detached: `docker compose up -d` (y `docker compose logs -f` para ver).
- Para un estado 100% limpio usá `docker compose down && docker compose up -d` (un simple
  `restart` **no** borra los parámetros que SITL guarda en su eeprom).
- Si el visor muestra el enjambre "sano" pero sabés que lanzaste el modo firmware, puede haber
  un server viejo ocupando el 8010. Antes de relanzar, matá todo lo que escuche ahí:
  `Get-NetTCPConnection -LocalPort 8010 -State Listen | %{ Stop-Process -Id $_.OwningProcess -Force }`
- El primer arranque de los 7 drones tarda ~60-90 s (esperan fix GPS y confirman armado).

## El núcleo matemático

- **Voto:** el vecino *j* vota contra *i* si `| dist(GPS_i, GPS_j) − rango_medido(i,j) | > umbral`.
  Un dron spoofeado acumula votos de **todos** sus vecinos a la vez; uno sano, casi ninguno.
- **Consenso:** si los votos ≥ mayoría de vecinos (sostenido) → nodo comprometido.
- **Corrección:** posición real por multilateración, minimizando
  `Σ_j ( dist(p, GPS_j) − rango_medido(i,j) )²` sobre los vecinos sanos (descenso por gradiente).

## Extensiones futuras

- ~~Ingesta MAVLink real (PX4/ArduPilot)~~ ✅ **hecho** (backend `mav`, ver arriba). Falta
  el salto a Pixhawk físico + companion computer (mismo motor, se reemplaza SITL por el enlace serie/UDP real).
- Correción por reinyección directa al EKF real vía `GPS_INPUT` / selección de fuente de navegación
  (hoy la corrección se comanda como setpoint compensado).
- Fusión con IMU/EKF para robustez ante spoofing sutil (deriva lenta).
- Detección de jamming y de degradación del enlace (comportamiento seguro / RTL a ciegas).
- Reasignación de misión del enjambre ante bajas (formación adaptativa).
```
