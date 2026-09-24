"""
Data access layer — all DB interactions go through here.
SQLAlchemy ORM is used exclusively; raw SQL strings are forbidden to prevent injection.
PII columns (name, phone, email, notes) are encrypted at rest via EncryptionManager.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Literal

import pytz
from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.orm import selectinload

from app.db.models import Appointment, Reminder, User
from app.security.crypto import EncryptionManager

_TZ = pytz.timezone("America/Bogota")
_BUSINESS_HOURS = range(7, 18)  # 07:00–17:30 (last slot at 17:30 for 30-min duration)
_SLOT_MINUTES = 30


class AppointmentRepository:
    def __init__(self, session: AsyncSession, crypto: EncryptionManager) -> None:
        self._s = session
        self._c = crypto

    # ── Users ──────────────────────────────────────────────────────────────

    async def get_or_create_user(
        self, user_id: str, full_name: str, phone: str | None = None, email: str | None = None
    ) -> User:
        result = await self._s.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            user = User(
                id=user_id,
                full_name_enc=self._c.encrypt(full_name),
                phone_enc=self._c.encrypt_optional(phone),
                email_enc=self._c.encrypt_optional(email),
            )
            self._s.add(user)
            await self._s.flush()
        return user

    async def get_user(self, user_id: str) -> User | None:
        result = await self._s.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    def decrypt_user(self, user: User) -> dict:
        return {
            "id": user.id,
            "full_name": self._c.decrypt(user.full_name_enc),
            "phone": self._c.decrypt_optional(user.phone_enc),
            "email": self._c.decrypt_optional(user.email_enc),
            "timezone": user.timezone,
        }

    # ── Appointments ────────────────────────────────────────────────────────

    async def count_active(self, user_id: str) -> int:
        result = await self._s.execute(
            select(Appointment).where(
                and_(Appointment.user_id == user_id, Appointment.status == "active")
            )
        )
        return len(result.scalars().all())

    async def is_slot_available(self, scheduled_at: datetime, provider_id: str | None = None) -> bool:
        """Check no other active appointment overlaps this 30-min slot."""
        end = scheduled_at + timedelta(minutes=_SLOT_MINUTES)
        filters = [
            Appointment.status == "active",
            Appointment.scheduled_at < end,
            (Appointment.scheduled_at + timedelta(minutes=_SLOT_MINUTES)) > scheduled_at,
        ]
        if provider_id:
            filters.append(Appointment.provider_id == provider_id)
        result = await self._s.execute(select(Appointment).where(and_(*filters)))
        return result.scalar_one_or_none() is None

    async def create(
        self,
        user_id: str,
        service_type: str,
        scheduled_at: datetime,
        notes: str | None = None,
        provider_id: str | None = None,
    ) -> Appointment:
        appt = Appointment(
            user_id=user_id,
            service_type=service_type,
            scheduled_at=scheduled_at,
            provider_id=provider_id,
            notes_enc=self._c.encrypt_optional(notes),
        )
        self._s.add(appt)
        await self._s.flush()
        return appt

    async def get(self, appointment_id: uuid.UUID, user_id: str) -> Appointment | None:
        """Fetch an appointment only if it belongs to the requesting user."""
        result = await self._s.execute(
            select(Appointment).where(
                and_(Appointment.id == appointment_id, Appointment.user_id == user_id)
            )
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: str,
        status: Literal["active", "cancelled", "completed", "all"] = "active",
        limit: int = 5,
    ) -> list[Appointment]:
        q = select(Appointment).where(Appointment.user_id == user_id)
        if status != "all":
            q = q.where(Appointment.status == status)
        q = q.order_by(Appointment.scheduled_at).limit(max(1, min(limit, 20)))
        result = await self._s.execute(q)
        return list(result.scalars().all())

    async def cancel(
        self,
        appointment_id: uuid.UUID,
        user_id: str,
        reason: str | None = None,
    ) -> Appointment | None:
        appt = await self.get(appointment_id, user_id)
        if not appt or appt.status != "active":
            return None
        now = datetime.now(_TZ)
        if appt.scheduled_at.tzinfo is None:
            appt.scheduled_at = _TZ.localize(appt.scheduled_at)
        if (appt.scheduled_at - now).total_seconds() < 7200:  # 2h rule
            return None  # caller handles error message
        appt.status = "cancelled"
        appt.cancelled_at = now
        appt.cancel_reason_enc = self._c.encrypt_optional(reason)
        await self._s.flush()
        return appt

    async def reschedule(
        self,
        appointment_id: uuid.UUID,
        user_id: str,
        new_dt: datetime,
        reason: str | None = None,
    ) -> tuple[Appointment, Appointment] | None:
        """Returns (old_cancelled, new_active) or None on failure."""
        old = await self.get(appointment_id, user_id)
        if not old or old.status != "active":
            return None
        old.status = "rescheduled"
        new_appt = Appointment(
            user_id=user_id,
            service_type=old.service_type,
            scheduled_at=new_dt,
            provider_id=old.provider_id,
            original_id=old.id,
            notes_enc=old.notes_enc,
        )
        self._s.add(new_appt)
        await self._s.flush()
        return old, new_appt

    def format_appointment(self, appt: Appointment) -> dict:
        return {
            "id": str(appt.id),
            "service_type": appt.service_type,
            "scheduled_at": appt.scheduled_at.isoformat(),
            "duration_min": appt.duration_min,
            "status": appt.status,
            "provider_id": appt.provider_id,
        }

    # ── Availability ─────────────────────────────────────────────────────────

    async def get_available_slots(
        self,
        service_type: str,
        date_from: datetime,
        date_to: datetime,
        provider_id: str | None = None,
    ) -> list[dict]:
        """Generate slots and exclude already-booked ones."""
        slots: list[dict] = []
        cursor = date_from.replace(hour=7, minute=0, second=0, microsecond=0)
        end_boundary = date_to.replace(hour=18, minute=0, second=0, microsecond=0)

        while cursor <= end_boundary:
            if cursor.weekday() < 6:  # Mon–Sat
                available = await self.is_slot_available(cursor, provider_id)
                if available:
                    slots.append({
                        "datetime_iso": cursor.isoformat(),
                        "label": cursor.strftime("%A %d de %B a las %H:%M"),
                    })
            cursor += timedelta(minutes=_SLOT_MINUTES)
            if cursor.hour >= 18:
                cursor = (cursor + timedelta(days=1)).replace(hour=7, minute=0, second=0)

        return slots[:10]  # Return max 10 slots

    # ── Reminders ─────────────────────────────────────────────────────────────

    async def create_reminder(
        self,
        appointment_id: uuid.UUID,
        scheduled_for: datetime,
        channel: str,
    ) -> Reminder:
        reminder = Reminder(
            appointment_id=appointment_id,
            scheduled_for=scheduled_for,
            channel=channel,
        )
        self._s.add(reminder)
        await self._s.flush()
        return reminder

    async def claim_pending_reminders(
        self, before: datetime, limit: int = 100
    ) -> list[Reminder]:
        """Reclama recordatorios vencidos para esta transaccion.

        FOR UPDATE SKIP LOCKED: si dos ejecuciones del cron se solapan (la
        anterior aun corriendo cuando arranca la siguiente), la segunda salta
        las filas que la primera ya tiene tomadas en vez de enviarlas otra vez.
        Sin esto el paciente recibe el recordatorio duplicado.

        Carga cita y usuario por adelantado: en SQLAlchemy async un acceso
        perezoso a la relacion dentro del bucle lanza MissingGreenlet.

        ponytail: las filas quedan bloqueadas mientras se envia, asi que el
        lote va acotado con `limit`. Si el volumen crece hasta que una tanda
        tarda demasiado, el paso siguiente es un estado 'sending' persistido
        (nueva migracion) para soltar el bloqueo antes de llamar al proveedor.
        """
        result = await self._s.execute(
            select(Reminder)
            .where(
                and_(
                    Reminder.status == "pending",
                    Reminder.scheduled_for <= before,
                )
            )
            .order_by(Reminder.scheduled_for)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        recordatorios = list(result.scalars().all())

        if not recordatorios:
            return []

        # Segunda consulta para las relaciones: with_for_update no admite
        # el OUTER JOIN que genera selectinload en la misma sentencia.
        ids = [r.id for r in recordatorios]
        await self._s.execute(
            select(Reminder)
            .where(Reminder.id.in_(ids))
            .options(selectinload(Reminder.appointment).selectinload(Appointment.user))
        )
        return recordatorios

    async def get_pending_reminders(self, before: datetime) -> list[Reminder]:
        result = await self._s.execute(
            select(Reminder).where(
                and_(
                    Reminder.status == "pending",
                    Reminder.scheduled_for <= before,
                )
            )
        )
        return list(result.scalars().all())
