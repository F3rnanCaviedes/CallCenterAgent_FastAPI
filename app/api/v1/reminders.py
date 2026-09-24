"""
Disparo del barrido de recordatorios.

Lo llama un cron interno, no un paciente. Por eso NO usa require_auth: un JWT
de usuario es cualquier token válido del sistema, y con eso cualquier paciente
autenticado podía disparar el envío masivo. Va con clave de servicio propia,
separada de las claves que emiten tokens de usuario, para que comprometer el
backend del portal no habilite además el envío de notificaciones.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime
from typing import Annotated

import pytz
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.repository import AppointmentRepository
from app.db.session import get_db
from app.services.notifications import NotificationError, enviar_recordatorio

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/reminders", tags=["reminders"])

_TZ = pytz.timezone("America/Bogota")


async def require_internal_key(
    x_internal_key: Annotated[str | None, Header(alias="X-Internal-Key")] = None,
) -> None:
    claves = get_settings().internal_api_keys
    if not claves:
        raise HTTPException(status_code=503, detail="Disparo interno no configurado")
    if not x_internal_key:
        raise HTTPException(status_code=401, detail="Clave interna requerida")
    # Recorre la lista entera: cortar en el acierto filtraria la posicion.
    ok = False
    for clave in claves:
        if secrets.compare_digest(x_internal_key, clave.get_secret_value()):
            ok = True
    if not ok:
        raise HTTPException(status_code=401, detail="Clave interna inválida")


def _mensaje(cita, nombre: str) -> tuple[str, str]:
    cuando = cita.scheduled_at.astimezone(_TZ).strftime("%d/%m/%Y a las %I:%M %p")
    cuerpo = (
        f"Hola {nombre}, te recordamos tu cita de {cita.service_type} "
        f"el {cuando}. Si necesitas cancelar o reprogramar, responde a este mensaje."
    )
    return "Recordatorio de tu cita", cuerpo


@router.post("/trigger", dependencies=[Depends(require_internal_key)])
async def trigger_reminders(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Envía los recordatorios vencidos. Idempotente entre ejecuciones solapadas."""
    crypto = request.app.state.crypto
    repo = AppointmentRepository(db, crypto)
    ahora = datetime.now(_TZ)

    pendientes = await repo.claim_pending_reminders(ahora)

    enviados, fallidos = 0, 0
    for recordatorio in pendientes:
        cita = recordatorio.appointment
        usuario = cita.user if cita else None
        try:
            if usuario is None:
                raise NotificationError("recordatorio sin cita o sin usuario")

            datos = repo.decrypt_user(usuario)
            destino = datos["phone"] if recordatorio.channel != "email" else datos["email"]
            asunto, cuerpo = _mensaje(cita, datos["full_name"])

            proveedor_id = await enviar_recordatorio(
                recordatorio.channel, destino, cuerpo, asunto
            )
            recordatorio.status = "sent"
            recordatorio.sent_at = ahora
            cita.reminder_sent = True
            enviados += 1
            logger.info(
                "recordatorio_enviado id=%s canal=%s proveedor_id=%s",
                recordatorio.id, recordatorio.channel, proveedor_id,
            )
        except Exception as exc:  # noqa: BLE001
            # Un fallo de un recordatorio no puede tumbar la tanda entera.
            recordatorio.status = "failed"
            fallidos += 1
            logger.warning(
                "recordatorio_fallido id=%s canal=%s error=%s",
                recordatorio.id, recordatorio.channel, type(exc).__name__,
            )

    return {"processed": len(pendientes), "sent": enviados, "failed": fallidos}
