"""
Conversión entre el marco local de la formación y coordenadas geográficas (LLA).

El motor de FALANGE trabaja en un marco local plano en metros (x=Este, y=Norte,
z=arriba). ArduPilot habla en latitud/longitud/altitud. Estas funciones traducen
entre ambos con la aproximación de plano tangente (equirrectangular), suficiente
para un enjambre que opera en un radio de pocos cientos de metros.

HOME es el punto de origen del marco local sobre el terreno. Se ubica en un campo
de la provincia de Córdoba (cerca del polo aeroespacial de FAdeA) — es sólo el
ancla del sistema local; se puede reubicar sin tocar el resto del código.
"""
import math

# Origen del marco local sobre el terreno (lat, lon, alt en m sobre el nivel del mar).
HOME_LAT = -31.4370
HOME_LON = -64.1888
HOME_ALT = 470.0

_R_EARTH = 6378137.0  # radio terrestre (m), esferoide WGS-84 simplificado


def enu_to_lla(x, y, z, home=(HOME_LAT, HOME_LON, HOME_ALT)):
    """Marco local (Este, Norte, arriba) en metros -> (lat, lon, alt)."""
    lat0, lon0, alt0 = home
    dlat = (y / _R_EARTH) * (180.0 / math.pi)
    dlon = (x / (_R_EARTH * math.cos(math.radians(lat0)))) * (180.0 / math.pi)
    return lat0 + dlat, lon0 + dlon, alt0 + z


def lla_to_enu(lat, lon, alt, home=(HOME_LAT, HOME_LON, HOME_ALT)):
    """(lat, lon, alt) -> marco local (Este, Norte, arriba) en metros."""
    lat0, lon0, alt0 = home
    x = math.radians(lon - lon0) * _R_EARTH * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * _R_EARTH
    return x, y, alt - alt0
