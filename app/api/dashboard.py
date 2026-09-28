"""
Dashboard operativo.

  GET /dashboard       página estática. No lleva datos: pide la clave interna
                       en el navegador y consulta /dashboard/data con ella.
  GET /dashboard/app.js  frontend compilado desde dashboard/src (TypeScript).
  GET /dashboard/data  JSON con salud, llamadas, citas e integraciones.
                       Protegido con X-Internal-Key, igual que el cron.

Nada de datos de pacientes: sólo conteos y estados. Las integraciones se
reportan como "configurada sí/no" — sondear Deepgram o Azure en cada refresco
gastaría cuota y no dice nada que la primera llamada no diga mejor.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import func, select

from app.api.health import _check_postgres, _check_redis, _sondear
from app.api.v1.reminders import _TZ, require_internal_key
from app.config import get_settings
from app.db.models import Appointment, Reminder
from app.db.session import get_session_factory
from app.services.browser import navegador

logger = logging.getLogger(__name__)
router = APIRouter(tags=["dashboard"], include_in_schema=False)

_INICIO = time.time()

_DIR = Path(__file__).parent
_HTML = (_DIR / "dashboard.html").read_text(encoding="utf-8")
# Lo genera `npm run build` en dashboard/ (en Docker, la etapa de Node).
_JS = _DIR / "static" / "dashboard.js"
# Sólo scripts del propio origen: nada inline, nada de terceros.
_CSP = (
    "default-src 'none'; "
    "script-src 'self'; "
    "style-src 'unsafe-inline'; "
    "connect-src 'self'; "
    "frame-ancestors 'none'"
)


@router.get("/dashboard")
async def pagina() -> HTMLResponse:
    return HTMLResponse(_HTML, headers={"Content-Security-Policy": _CSP})


@router.get("/dashboard/app.js")
async def script() -> FileResponse:
    if not _JS.is_file():
        logger.error("dashboard_sin_compilar ruta=%s", _JS)
        raise HTTPException(status_code=503, detail="Dashboard sin compilar: npm run build en dashboard/")
    return FileResponse(_JS, media_type="text/javascript")


async def _citas() -> dict:
    ahora = datetime.now(_TZ)
    inicio_dia = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
    async with get_session_factory()() as db:
        hoy = await db.execute(
            select(Appointment.status, func.count())
            .where(Appointment.created_at >= inicio_dia)
            .group_by(Appointment.status)
        )
        proximas = await db.scalar(
            select(func.count()).select_from(Appointment).where(
                Appointment.status == "active",
                Appointment.scheduled_at.between(ahora, ahora + timedelta(hours=24)),
            )
        )
        recordatorios = await db.execute(
            select(Reminder.status, func.count()).group_by(Reminder.status)
        )
    return {
        "created_today": dict(hoy.all()),
        "next_24h": proximas,
        "reminders": dict(recordatorios.all()),
    }


async def _o_error(corutina):
    """Un bloque caído no tumba el dashboard entero: se reporta y sigue."""
    try:
        return await asyncio.wait_for(corutina, timeout=3.0)
    except Exception as exc:  # noqa: BLE001
        return {"error": type(exc).__name__}


def _integraciones() -> dict:
    s = get_settings()
    llm = s.anthropic_api_key if s.llm_provider == "anthropic" else s.openai_api_key
    return {
        "llm": bool(llm and llm.get_secret_value()),
        "twilio": bool(s.twilio_account_sid and s.twilio_auth_token),
        "deepgram": bool(s.deepgram_api_key),
        "azure_tts": bool(s.azure_tts_key and s.azure_tts_region),
        "sendgrid": bool(s.sendgrid_api_key),
    }


@router.get("/dashboard/data", dependencies=[Depends(require_internal_key)])
async def datos(request: Request) -> dict:
    s = get_settings()
    pg, rd, llamadas, citas = await asyncio.gather(
        _sondear("postgres", _check_postgres()),
        _sondear("redis", _check_redis(request)),
        _o_error(request.app.state.sessions.leer_metricas()),
        _o_error(_citas()),
    )
    dependencias = {nombre: ("ok" if err is None else err) for nombre, err in (pg, rd)}

    if not s.form_filler_allowed_hosts:
        dependencias["browser"] = "desactivado"
    else:
        dependencias["browser"] = "ok" if navegador.conectado else "caido"

    caidas = [n for n, v in dependencias.items() if v not in ("ok", "desactivado")]
    return {
        "status": "degraded" if caidas else "ok",
        "env": s.app_env,
        "uptime_s": int(time.time() - _INICIO),
        "server_time": datetime.now(_TZ).isoformat(),
        "llm": {"provider": s.llm_provider, "model": s.llm_model},
        "dependencies": dependencias,
        "integrations": _integraciones(),
        "calls": llamadas,
        "appointments": citas,
    }
