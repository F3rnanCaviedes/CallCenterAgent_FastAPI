"""
Emisión de tokens de acceso.

Modelo de confianza: el llamante es un backend de cliente (el servidor del
portal de la clínica), no el navegador del paciente. Ese backend ya autenticó
a su usuario y presenta una clave de servicio para pedir un JWT a nombre de
él. Por lo tanto quien tiene la clave puede emitir tokens para CUALQUIER
user_id — es la propiedad esperada de este diseño, y la razón por la que la
clave nunca debe viajar al navegador ni a una app móvil.
"""
from __future__ import annotations

import logging
import secrets
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.config import get_settings
from app.security.auth import create_api_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/auth", tags=["auth"])

_ALLOWED_UID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)


class TokenRequest(BaseModel):
    user_id: Annotated[str, Field(min_length=1, max_length=100)]

    @field_validator("user_id")
    @classmethod
    def validate_uid(cls, v: str) -> str:
        if not all(c in _ALLOWED_UID_CHARS for c in v):
            raise ValueError("user_id inválido")
        return v


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int


def _verify_client_key(presented: str | None) -> bool:
    """Compara contra las claves configuradas en tiempo constante.

    Recorre la lista completa siempre: cortar en el primer acierto filtraría
    la posición de la clave a través del tiempo de respuesta.
    """
    settings = get_settings()
    keys = settings.client_api_keys
    if not keys or not presented:
        return False
    matched = False
    for key in keys:
        if secrets.compare_digest(presented, key.get_secret_value()):
            matched = True
    return matched


@router.post("/token", response_model=TokenResponse)
async def issue_token(
    body: TokenRequest,
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> TokenResponse:
    settings = get_settings()

    if not settings.client_api_keys:
        # Sin claves configuradas el endpoint queda cerrado, no abierto.
        logger.error("auth_token_sin_claves_configuradas")
        raise HTTPException(
            status_code=503, detail="Emisión de tokens no configurada"
        )

    if not _verify_client_key(x_api_key):
        logger.warning(
            "auth_token_clave_invalida ip=%s user_id=%s",
            request.client.host if request.client else "unknown",
            body.user_id,
        )
        raise HTTPException(status_code=401, detail="Clave de cliente inválida")

    ttl = settings.access_token_ttl_seconds
    token = create_api_token(body.user_id, expires_in=ttl)
    logger.info("auth_token_emitido user_id=%s ttl=%d", body.user_id, ttl)
    return TokenResponse(access_token=token, expires_in=ttl)
