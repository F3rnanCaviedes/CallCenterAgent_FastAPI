from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker | None = None


def _get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        url = settings.database_url.get_secret_value()
        # Force asyncpg driver
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        _engine = create_async_engine(
            url,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=300,
            echo=not settings.is_production,
        )
    return _engine


def _get_factory() -> async_sessionmaker:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=_get_engine(),
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )
    return _session_factory


def get_session_factory() -> async_sessionmaker:
    """Factoria de sesiones para quien no puede usar Depends(get_db),
    como las sondas de salud."""
    return _get_factory()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with _get_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None
