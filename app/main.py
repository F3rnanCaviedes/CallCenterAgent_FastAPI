"""
VoiceScheduler Agent — Sofía
Entry point: FastAPI application with security middleware, lifespan management,
rate limiting, CORS, and singleton agent state.
"""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.agent.core import AgentCore
from app.agent.session import SessionManager
from app.api import health
from app.api.limiter import limiter
from app.api.middleware import RequestAuditMiddleware, SecurityHeadersMiddleware
from app.api.v1 import appointments, auth, chat, reminders, voice
from app.config import get_settings
from app.db.session import dispose_engine
from app.security.crypto import EncryptionManager
from app.services.browser import navegador

# ── Structured logging ────────────────────────────────────────────────────

def _configure_logging(level: str) -> None:
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
    )


# ── Lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _configure_logging(settings.log_level)

    crypto = EncryptionManager(settings.encryption_key.get_secret_value())
    session_manager = SessionManager(crypto)
    agent = AgentCore(session_manager, crypto)

    app.state.crypto = crypto
    app.state.sessions = session_manager
    app.state.agent = agent

    # Chromium arranca con la aplicacion, no dentro de la llamada: lanzarlo
    # bajo demanda mete 1-3 s de silencio mientras el paciente espera.
    if settings.form_filler_allowed_hosts:
        await navegador.iniciar()
    else:
        logging.getLogger(__name__).info(
            "form_filler desactivado: no se arranca Chromium"
        )

    yield

    await session_manager.close()
    await navegador.cerrar()
    await dispose_engine()


# ── App factory ───────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="VoiceScheduler Agent — Sofía",
        version="1.0.0",
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
        lifespan=lifespan,
    )

    # Rate limiter — el middleware es lo que realmente aplica los límites;
    # sin él los decoradores @limiter.limit no se evalúan.
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    # CORS — explicit allowlist only
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        max_age=600,
    )

    # Security + audit middleware (outermost = last applied)
    app.add_middleware(RequestAuditMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    # Routers
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(chat.router)
    app.include_router(appointments.router)
    app.include_router(reminders.router)
    app.include_router(voice.router)

    # Global exception handler — never leak stack traces
    @app.exception_handler(Exception)
    async def _global_handler(request: Request, exc: Exception) -> JSONResponse:
        logging.getLogger(__name__).error("unhandled_exception path=%s error=%s", request.url.path, exc)
        return JSONResponse(
            status_code=500,
            content={"detail": "Error interno del servidor. Por favor intenta más tarde."},
        )

    return app


app = create_app()
