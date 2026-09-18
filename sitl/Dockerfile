# ---------------------------------------------------------------------------
# FALANGE — Imagen de FIRMWARE REAL: enjambre de ArduPilot SITL (ArduCopter).
#
# Trae el firmware de vuelo real ya compilado. Al correr, levanta N=7 instancias
# de ArduCopter y las expone por MAVLink/TCP en 5760+10*i — exactamente los
# endpoints que el backend MavWorld busca por defecto.
#
# Construir (una vez, tarda ~15-30 min la primera vez):
#   cd Idea1/sitl && docker compose build
# Correr:
#   docker compose up
# ---------------------------------------------------------------------------
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=UTC \
    PIP_BREAK_SYSTEM_PACKAGES=1

# Prerrequisitos mínimos para clonar y correr el instalador oficial de ArduPilot.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git sudo lsb-release tzdata software-properties-common ca-certificates \
        python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

# ArduPilot se niega a compilarse como root: creamos un usuario con sudo sin clave.
RUN useradd -m -s /bin/bash ap \
    && echo 'ap ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/ap \
    && chmod 0440 /etc/sudoers.d/ap

# Firmware: ArduPilot, rama estable Copter-4.5, con submódulos.
ARG AP_BRANCH=Copter-4.5
RUN git clone --depth 1 -b ${AP_BRANCH} https://github.com/ArduPilot/ardupilot.git /ardupilot \
    && chown -R ap:ap /ardupilot

USER ap
ENV USER=ap HOME=/home/ap
WORKDIR /ardupilot
RUN git config --global --add safe.directory /ardupilot \
    && git submodule update --init --recursive --depth 1

# Toolchain + dependencias (instalador oficial de ArduPilot) y build de SITL.
RUN Tools/environment_install/install-prereqs-ubuntu.sh -y
RUN bash -lc '. ~/.profile && ./waf configure --board sitl && ./waf copter'

ENV PATH="/ardupilot/Tools/autotest:/ardupilot/build/sitl/bin:/home/ap/.local/bin:${PATH}"

COPY --chown=ap:ap entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Puertos MAVLink de las 7 instancias (5760,5770,...,5820).
EXPOSE 5760 5770 5780 5790 5800 5810 5820
ENTRYPOINT ["/entrypoint.sh"]
