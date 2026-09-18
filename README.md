# FALANGE

**El enjambre de drones que se cuida solo.**
Navegación por consenso: detección y corrección de spoofing GPS distribuida en un enjambre.

> Hackathon Nacional de Ciberdefensa **CYBER.AR 2026** — Eje 1 (sistemas autónomos y no tripulados).

---

## El problema

Con **spoofing de GPS**, un atacante inyecta señales falsas y "secuestra" un dron: lo
desvía físicamente hacia una trampa mientras el dron cree que sigue en curso. Un dron
**solo** no puede saber si su GPS le miente.

## La idea

Los drones se **miden entre sí por radio** (ranging tipo UWB). Esa distancia física **no se
puede falsificar desde lejos**. Si el GPS de un dron contradice lo que mide todo el enjambre,
los vecinos **votan**, lo **aíslan** y **reconstruyen su posición real**. Es un *RAIM
distribuido*: en vez de cruzar satélites, cruza vecinos.

> *"No confían en el satélite. Confían entre ellos."*

## Cómo correr

Requisito: **Python 3.8+**. No hay que instalar nada.

```bash
python run.py
```

Abre `http://127.0.0.1:8010/`. Apretá **▶ Demostración guiada** (o la tecla `G`) y se explica sola.

## Qué hace

- **Anti-spoofing por consenso** — detecta, aísla y corrige un dron spoofeado por votación
  bizantina + multilateración.
- **Resiliencia autónoma del enjambre** (botones + escenarios guiados):
  - Voto para **apagar el GPS** de todo el enjambre ante spoofing masivo → vuelo inercial/UWB.
  - **Relevo de comunicaciones** (dron puente) y retorno coordinado ante jamming.
  - **Relevo de liderazgo** por batería.
  - **Dispersión táctica** y reagrupe en punto de reunión.
- **Firmware real** — el mismo motor corre sobre **7 instancias de ArduPilot SITL** (el
  firmware que vuela en un Pixhawk). Ver [DOCS.md](DOCS.md).

## Modo firmware real (opcional)

Corre el firmware de vuelo real (ArduPilot) en Docker y FALANGE lo vigila:

```bash
cd sitl && docker compose up -d      # 7 ArduCopter reales
../run-mav.ps1                        # FALANGE en modo firmware
```

Detalles, arquitectura y troubleshooting: **[DOCS.md](DOCS.md)**.
