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

## Modo firmware real (opcional)

Corre el firmware de vuelo real (ArduPilot) en Docker y Lhok lo vigila:

```bash
cd sitl && docker compose up -d      # 7 ArduCopter reales
../run-mav.ps1                        # FALANGE en modo firmware
```

Detalles, arquitectura y troubleshooting: **[DOCS.md](DOCS.md)**.
