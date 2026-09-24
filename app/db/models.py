import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(String(100), primary_key=True)
    full_name_enc = Column(String(500), nullable=False)        # Encrypted
    phone_enc = Column(String(500), nullable=True)             # Encrypted
    email_enc = Column(String(500), nullable=True)             # Encrypted
    timezone = Column(String(50), default="America/Bogota", nullable=False)
    preferences = Column(JSONB, default=dict, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    appointments = relationship("Appointment", back_populates="user", lazy="select")


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','cancelled','completed','rescheduled')",
            name="ck_appointments_status",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(String(100), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider_id = Column(String(100), nullable=True)
    service_type = Column(String(100), nullable=False)
    scheduled_at = Column(DateTime(timezone=True), nullable=False)
    duration_min = Column(Integer, default=30, nullable=False)
    status = Column(String(20), default="active", nullable=False)
    notes_enc = Column(Text, nullable=True)                    # Encrypted
    original_id = Column(UUID(as_uuid=True), ForeignKey("appointments.id"), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancel_reason_enc = Column(Text, nullable=True)            # Encrypted
    reminder_sent = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User", back_populates="appointments")
    reminders = relationship("Reminder", back_populates="appointment", lazy="select")


class Reminder(Base):
    __tablename__ = "reminders"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('voice','sms','email','whatsapp')",
            name="ck_reminders_channel",
        ),
        CheckConstraint(
            "status IN ('pending','sent','failed')",
            name="ck_reminders_status",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    appointment_id = Column(UUID(as_uuid=True), ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False)
    scheduled_for = Column(DateTime(timezone=True), nullable=False)
    channel = Column(String(20), nullable=False)
    status = Column(String(20), default="pending", nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    appointment = relationship("Appointment", back_populates="reminders")
