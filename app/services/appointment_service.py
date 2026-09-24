"""
Business logic layer — bridges the AI agent tool calls with the repository.
Enforces business rules before mutating data.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

import pytz
from dateutil.parser import parse as parse_dt
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import AppointmentRepository
from app.security.crypto import EncryptionManager
from app.security.sanitizer import sanitize_field

_TZ = pytz.timezone("America/Bogota")
_VALID_CHANNELS = {"voice", "sms", "email", "whatsapp"}
_MAX_ACTIVE_APPOINTMENTS = 3


def _parse_iso(dt_string: str) -> datetime:
    try:
        dt = parse_dt(dt_string)
        if dt.tzinfo is None:
            dt = _TZ.localize(dt)
        return dt
    except Exception:
        raise ValueError(f"Fecha inválida: {dt_string!r}")


def _is_business_hours(dt: datetime) -> bool:
    local = dt.astimezone(_TZ)
    return local.weekday() < 6 and 7 <= local.hour < 18


class AppointmentService:
    def __init__(self, session: AsyncSession, crypto: EncryptionManager) -> None:
        self._repo = AppointmentRepository(session, crypto)

    async def create_appointment(
        self,
        user_id: str,
        service_type: str,
        datetime_iso: str,
        notes: str | None = None,
        provider_id: str | None = None,
    ) -> dict:
        service_type = sanitize_field(service_type, 100)
        notes = sanitize_field(notes, 500) if notes else None
        provider_id = sanitize_field(provider_id, 100) if provider_id else None

        dt = _parse_iso(datetime_iso)

        if not _is_business_hours(dt):
            return {"success": False, "error": "slot_unavailable",
                    "message": "El horario está fuera del horario de atención (L–S 7 AM–6 PM)."}

        active_count = await self._repo.count_active(user_id)
        if active_count >= _MAX_ACTIVE_APPOINTMENTS:
            return {"success": False, "error": "max_appointments_reached",
                    "message": "Ya tienes 3 citas activas (máximo permitido)."}

        if not await self._repo.is_slot_available(dt, provider_id):
            return {"success": False, "error": "slot_unavailable",
                    "message": "Ese horario no está disponible."}

        appt = await self._repo.create(user_id, service_type, dt, notes, provider_id)

        # Schedule reminders at 24h and 1h before
        for hours_before in (24, 1):
            reminder_time = dt - timedelta(hours=hours_before)
            if reminder_time > datetime.now(_TZ):
                await self._repo.create_reminder(appt.id, reminder_time, "sms")

        return {"success": True, "appointment": self._repo.format_appointment(appt)}

    async def cancel_appointment(
        self,
        appointment_id_str: str,
        user_id: str,
        reason: str | None = None,
        notify_user: bool = True,
    ) -> dict:
        reason = sanitize_field(reason, 300) if reason else None
        try:
            appt_id = uuid.UUID(appointment_id_str)
        except ValueError:
            return {"success": False, "error": "not_found", "message": "ID de cita inválido."}

        appt = await self._repo.cancel(appt_id, user_id, reason)
        if appt is None:
            # Distinguish: not found vs. too late
            existing = await self._repo.get(appt_id, user_id)
            if not existing:
                return {"success": False, "error": "not_found",
                        "message": "No encontré esa cita en tu cuenta."}
            return {"success": False, "error": "cancellation_too_late",
                    "message": "Las cancelaciones deben hacerse con al menos 2 horas de anticipación."}

        return {"success": True, "appointment": self._repo.format_appointment(appt)}

    async def reschedule_appointment(
        self,
        appointment_id_str: str,
        user_id: str,
        new_datetime_iso: str,
        reason: str | None = None,
    ) -> dict:
        reason = sanitize_field(reason, 300) if reason else None
        try:
            appt_id = uuid.UUID(appointment_id_str)
        except ValueError:
            return {"success": False, "error": "not_found", "message": "ID de cita inválido."}

        new_dt = _parse_iso(new_datetime_iso)
        if not _is_business_hours(new_dt):
            return {"success": False, "error": "slot_unavailable",
                    "message": "El nuevo horario está fuera del horario de atención."}

        if not await self._repo.is_slot_available(new_dt):
            return {"success": False, "error": "slot_unavailable",
                    "message": "El nuevo horario no está disponible."}

        result = await self._repo.reschedule(appt_id, user_id, new_dt, reason)
        if not result:
            return {"success": False, "error": "not_found",
                    "message": "No encontré esa cita o ya fue cancelada."}

        _, new_appt = result
        return {"success": True, "appointment": self._repo.format_appointment(new_appt)}

    async def list_appointments(
        self, user_id: str, status: str = "active", limit: int = 5
    ) -> dict:
        status = status if status in {"active", "cancelled", "completed", "all"} else "active"
        limit = max(1, min(int(limit), 20))
        appts = await self._repo.list_for_user(user_id, status, limit)  # type: ignore[arg-type]
        return {
            "success": True,
            "appointments": [self._repo.format_appointment(a) for a in appts],
            "count": len(appts),
        }

    async def check_availability(
        self,
        service_type: str,
        date_from_str: str,
        date_to_str: str | None = None,
        provider_id: str | None = None,
    ) -> dict:
        service_type = sanitize_field(service_type, 100)
        dt_from = _parse_iso(date_from_str)
        dt_to = _parse_iso(date_to_str) if date_to_str else dt_from + timedelta(days=7)
        slots = await self._repo.get_available_slots(service_type, dt_from, dt_to, provider_id)
        return {"success": True, "slots": slots, "count": len(slots)}
