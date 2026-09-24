"""initial schema: users, appointments, reminders

Revision ID: 0001
Revises:
Create Date: 2026-09-21

Escrita a mano desde app/db/models.py (la base de datos de desarrollo no
estaba disponible para --autogenerate).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=100), primary_key=True),
        sa.Column("full_name_enc", sa.String(length=500), nullable=False),
        sa.Column("phone_enc", sa.String(length=500), nullable=True),
        sa.Column("email_enc", sa.String(length=500), nullable=True),
        sa.Column("timezone", sa.String(length=50), server_default="America/Bogota", nullable=False),
        sa.Column("preferences", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "appointments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("provider_id", sa.String(length=100), nullable=True),
        sa.Column("service_type", sa.String(length=100), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_min", sa.Integer(), server_default="30", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("notes_enc", sa.Text(), nullable=True),
        sa.Column("original_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason_enc", sa.Text(), nullable=True),
        sa.Column("reminder_sent", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["original_id"], ["appointments.id"]),
        sa.CheckConstraint(
            "status IN ('active','cancelled','completed','rescheduled')",
            name="ck_appointments_status",
        ),
    )
    # Índices para los accesos reales del repositorio: listar citas de un usuario
    # por estado y fecha, y buscar solapamientos al agendar.
    op.create_index(
        "ix_appointments_user_status_sched",
        "appointments",
        ["user_id", "status", "scheduled_at"],
    )
    op.create_index("ix_appointments_scheduled_at", "appointments", ["scheduled_at"])

    op.create_table(
        "reminders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("appointment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["appointment_id"], ["appointments.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "channel IN ('voice','sms','email','whatsapp')", name="ck_reminders_channel"
        ),
        sa.CheckConstraint(
            "status IN ('pending','sent','failed')", name="ck_reminders_status"
        ),
    )
    # El barrido de recordatorios filtra por status + scheduled_for <= now.
    op.create_index(
        "ix_reminders_status_scheduled_for", "reminders", ["status", "scheduled_for"]
    )


def downgrade() -> None:
    op.drop_index("ix_reminders_status_scheduled_for", table_name="reminders")
    op.drop_table("reminders")
    op.drop_index("ix_appointments_scheduled_at", table_name="appointments")
    op.drop_index("ix_appointments_user_status_sched", table_name="appointments")
    op.drop_table("appointments")
    op.drop_table("users")
