"""
Limitador compartido.

Vive en su propio módulo porque tanto main.py como los routers lo necesitan,
y que los routers importaran de main.py crearía un ciclo de importación.

Se usa `default_limits` en vez de decoradores `@limiter.limit` por endpoint:
el decorador de slowapi envuelve la función y FastAPI deja de ver las
anotaciones reales de los parámetros, con lo que el cuerpo de la petición
se interpreta como query param. El límite global aplica a todas las rutas
sin tocar ninguna firma.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import get_settings

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{get_settings().rate_limit_per_minute}/minute"],
)
