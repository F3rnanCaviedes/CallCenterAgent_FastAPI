from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.security.auth import require_auth
from app.services.appointment_service import AppointmentService

router = APIRouter(prefix="/v1/appointments", tags=["appointments"])


class AppointmentListResponse(BaseModel):
    appointments: list[dict]
    count: int


@router.get("/{user_id}", response_model=AppointmentListResponse)
async def get_appointments(
    user_id: str,
    status: str = "active",
    limit: int = 5,
    token_data: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
) -> AppointmentListResponse:
    # Enforce ownership: token subject must match requested user_id
    if token_data.get("sub") != user_id:
        raise HTTPException(status_code=403, detail="No autorizado")

    crypto = request.app.state.crypto
    svc = AppointmentService(db, crypto)
    result = await svc.list_appointments(user_id=user_id, status=status, limit=limit)
    return AppointmentListResponse(
        appointments=result["appointments"],
        count=result["count"],
    )
