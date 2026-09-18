# Lhok

**Sistema de proteccion independiente para enjambres de drones.**
Utilización del consenso para la detección y corrección de problemas comunes en los enjambres.

> Hackathon CyberAr 2026 — Eje 1 (sistemas autónomos y no tripulados).

---

## El problema

Los drones son suceptibles a interferencias por ejemplo el spoofing de GPS, esto causa 
grandes perdidas en cuanto a material, ya que los drones se desvian fisicamente y caen 
sin haber terminado sus tareas.

## Nuestra solucion

Utilizamos el ranging tipo UWB incluido en la mayoria de los drones para calcular la distancia física 
**para contrarestar las interferencias**. Si el GPS de un dron contradice lo que mide todo el enjambre,
los vecinos **votan**, lo **aíslan** y **reconstruyen su posición real**. Es un *RAIM
distribuido*: en vez de utilizar satelites o emplear nuevos recursos, utilizamos datos de los vecinos.

> *"Se cuidan entre ellos sin invervencion humana real."*

## Cómo correr

Requisito: **Python 3.8+**. No hay que instalar nada.

```bash
python run.py
```

Abre `http://127.0.0.1:8010/`. Apretá **▶ Demostración guiada** (o la tecla `G`) y podras ver todos los casos de uso de este software.

## Qué hace

- **Anti-spoofing por consenso** — detecta, aísla y corrige un dron spoofeado .
- **Resiliencia autónoma del enjambre** (botones + escenarios guiados):
  - Voto para **apagar el GPS** de todo el enjambre ante spoofing masivo → vuelo inercial/UWB.
  - **Relevo de comunicaciones** (dron puente) y retorno coordinado ante jamming.
  - **Relevo de liderazgo** por batería.
  - **Dispersión táctica** y reagrupe en punto de reunión.
- **Firmware real** — el mismo motor corre sobre **7 instancias de ArduPilot SITL** (el
  firmware que vuela en un Pixhawk) por lo que todo lo que sucede en esta simulacion es 100% veridico. Ver [DOCS.md](DOCS.md).

---

## Modo firmware real (opcional, con Docker)

La simulación de arriba (`python run.py`) no necesita nada. Este modo, en cambio, corre
el **firmware de vuelo real de ArduPilot** —el mismo que va en un Pixhawk— dentro de Docker,
y Lhok lo vigila. Es lo que hace que todo lo que ves sea 100% verídico.

### Requisitos (una sola vez)

1. **Docker Desktop** instalado y **abierto** (esperá la ballena 🐳 **verde**, abajo a la derecha).
   En Windows, Docker usa **WSL2** por detrás; si te lo pide, aceptá la instalación.
2. `pip install pymavlink` (solo para este modo; el simulador no lo necesita).

### Correrlo — un solo comando

```powershell
.\start-firmware.ps1
```

Ese script hace todo: **levanta los 7 ArduCopter, espera a que estén listos y arranca Lhok**.
Después abrí **http://127.0.0.1:8010/**. Para bajar los contenedores: `.\stop-firmware.ps1`.

> ⏱️ **La primera vez**, Docker compila el firmware: **tarda 15-30 min** y descarga ~2 GB
> (una sola vez). Las siguientes son inmediatas. Y los 7 drones tardan **~90 s** en armar y
> despegar tras arrancar — es normal.

### Si preferís hacerlo a mano (2 pasos)

```powershell
cd sitl; docker compose up -d      # 1) levanta los 7 ArduCopter (esperá ~90 s)
cd ..; .\run-mav.ps1               # 2) arranca Lhok en modo firmware
```

Si Lhok no ve los drones (o los ves sin despegar), cerrá y volvé a correr `run-mav.ps1`:
la segunda vez el firmware ya está caliente y arma los 7.

Detalles, arquitectura y troubleshooting completo: **[DOCS.md](DOCS.md)**.
