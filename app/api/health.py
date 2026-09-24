"""
Sondas de salud.

Dos endpoints separados a propósito:

  /health        liveness  — ¿el proceso responde? No toca dependencias.
  /health/ready  readiness — ¿puede atender tráfico? Comprueba Postgres y Redis.

Mezclarlos es el error clásico: si la sonda de liveness falla porque la base
de datos se cayó, el orquestador reinicia contenedores sanos en bucle y
empeora la caída en vez de aliviarla. La de readiness sí debe fallar: saca al
pod del balanceador hasta que la dependencia vuelva.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request, Response
from sqlalchemy import text

from app.db.session import get_session_factory

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])

# Una sonda que se cuelga es una sonda inútil: el orquestador se queda
# esperando en vez de recibir un fallo claro.
_TIMEOUT_S = 2.0


async def _check_postgres() -> None:
    async with get_session_factory()() as session:
        await session.execute(text("SELECT 1"))


async def _check_redis(request: Request) -> None:
    await request.app.state.sessions.ping()


async def _sondear(nombre: str, corutina) -> tuple[str, str | None]:
    try:
        await asyncio.wait_for(corutina, timeout=_TIMEOUT_S)
        return nombre, None
    except asyncio.TimeoutError:
        return nombre, f"sin respuesta en {_TIMEOUT_S}s"
    except Exception as exc:  # noqa: BLE001 — cualquier fallo es "no listo"
        return nombre, f"{type(exc).__name__}"


@router.get("/health", include_in_schema=False)
async def health() -> dict:
    """Liveness: el proceso está vivo. Nunca consulta dependencias."""
    return {"status": "ok", "agent": "sofia"}


@router.get("/health/ready", include_in_schema=False)
async def ready(response: Response, request: Request) -> dict:
    """Readiness: 200 sólo si Postgres y Redis responden."""
    resultados = await asyncio.gather(
        _sondear("postgres", _check_postgres()),
        _sondear("redis", _check_redis(request)),
    )

    # El detalle del fallo se reduce al nombre del tipo de excepción: un
    # mensaje completo de asyncpg filtra host, usuario y base de datos a
    # quien pueda alcanzar la sonda.
    dependencias = {nombre: ("ok" if err is None else err) for nombre, err in resultados}
    caidas = [nombre for nombre, err in resultados if err is not None]

    if caidas:
        response.status_code = 503
        logger.warning("readiness_fallo dependencias=%s", ",".join(caidas))
        return {"status": "degraded", "dependencies": dependencias}

    return {"status": "ready", "dependencies": dependencias}
