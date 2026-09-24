import secrets
import time
from typing import Optional

import jwt
from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError

from app.config import get_settings

_bearer = HTTPBearer(auto_error=False)

_ALGORITHM = "HS256"
_ALLOWED_SESSION_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789_-"
)


def _secret() -> str:
    return get_settings().api_secret_key.get_secret_value()


def create_api_token(user_id: str, expires_in: int = 3600) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + expires_in,
        "jti": secrets.token_hex(16),
    }
    return jwt.encode(payload, _secret(), algorithm=_ALGORITHM)


def verify_api_token(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            _secret(),
            algorithms=[_ALGORITHM],
            options={"require": ["sub", "exp", "iat", "jti"]},
        )
    except InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")


async def require_auth(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(_bearer),
) -> dict:
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=401,
            detail="Autenticación requerida",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return verify_api_token(credentials.credentials)


def validate_session_id(session_id: str) -> bool:
    if not session_id or not (8 <= len(session_id) <= 128):
        return False
    return all(c in _ALLOWED_SESSION_CHARS for c in session_id)
