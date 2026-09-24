from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.security.auth import require_auth, validate_session_id
from app.security.sanitizer import sanitize_user_input

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/agent", tags=["agent"])


class InputPayload(BaseModel):
    type: Literal["text"] = "text"
    content: Annotated[str, Field(min_length=1, max_length=2000)]

    @field_validator("content")
    @classmethod
    def sanitize(cls, v: str) -> str:
        return sanitize_user_input(v)


class ContextPayload(BaseModel):
    timezone: str = "America/Bogota"
    channel: Literal["web", "phone", "whatsapp"] = "web"


class ChatRequest(BaseModel):
    session_id: Annotated[str | None, Field(default=None, max_length=128)] = None
    user_id: Annotated[str, Field(min_length=1, max_length=100)]
    input: InputPayload
    context: ContextPayload = ContextPayload()

    @field_validator("session_id")
    @classmethod
    def validate_sid(cls, v: str | None) -> str | None:
        if v is not None and not validate_session_id(v):
            raise ValueError("session_id inválido")
        return v

    @field_validator("user_id")
    @classmethod
    def validate_uid(cls, v: str) -> str:
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
        if not v or not all(c in allowed for c in v):
            raise ValueError("user_id inválido")
        return v


class ChatResponse(BaseModel):
    session_id: str
    response: dict
    action_taken: dict | None = None


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(
    body: ChatRequest,
    request: Request,
    token_data: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    """
    Main conversational endpoint. JWT subject must match the requested user_id
    to prevent one user from impersonating another.
    """
    if token_data.get("sub") != body.user_id:
        logger.warning(
            "user_mismatch token_sub=%s body_uid=%s ip=%s",
            token_data.get("sub"),
            body.user_id,
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=403, detail="No autorizado para este usuario")

    session_id = body.session_id or f"sess_{body.user_id}"
    result = await request.app.state.agent.chat(
        session_id=session_id,
        user_id=body.user_id,
        user_input=body.input.content,
        db_session=db,
    )
    return ChatResponse(**result)
