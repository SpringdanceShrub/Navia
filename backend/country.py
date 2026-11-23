from functools import lru_cache
import json
import math
from pathlib import Path

import shapely
from shapely.geometry import Point, shape


OUTSIDE_COUNTRY = (
    'No se pueden calcular rutas fuera de El Salvador. '
    'Selecciona el inicio y el destino dentro del país.'
)


@lru_cache(maxsize=1)
def country_boundary():
    path = Path(__file__).resolve().parents[1] / 'data' / 'el_salvador.geojson'
    data = json.loads(path.read_text(encoding='utf-8'))
    boundary = shape(data['features'][0]['geometry'])
    shapely.prepare(boundary)
    return boundary


def validate_route_point(coordinates):
    if not isinstance(coordinates, (list, tuple)) or len(coordinates) != 2:
        raise ValueError('Coordenadas de inicio y fin inválidas.')
    try:
        lon, lat = map(float, coordinates)
    except (ValueError, TypeError, OverflowError):
        raise ValueError('Coordenadas de inicio y fin inválidas.') from None
    if not all(map(math.isfinite, (lon, lat))) or not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError('Coordenadas de inicio y fin inválidas.')
    if not country_boundary().covers(Point(lon, lat)):
        raise ValueError(OUTSIDE_COUNTRY)
    return [lon, lat]
