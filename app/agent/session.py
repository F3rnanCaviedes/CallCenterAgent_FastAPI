"""
Session management using Redis.
Conversation history is stored encrypted; session IDs are cryptographically random.
"""
from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis

from app.config import get_settings
from app.security.crypto import EncryptionManager, generate_secure_token

_MAX_TURNS = 40  # Hard cap on stored messages to bound memory usage


class SessionManager:
    def __init__(self, crypto: EncryptionManager) -> None:
        self._crypto = crypto
        self._redis: aioredis.Redis | None = None

    def _client(self) -> aioredis.Redis:
        if self._redis is None:
            settings = get_settings()
            self._redis = aioredis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        return self._redis

    def _history_key(self, session_id: str) -> str:
        return f"sofia:session:{session_id}:history"

    def _meta_key(self, session_id: str) -> str:
        return f"sofia:session:{session_id}:meta"

    async def create_session(self, user_id: str) -> str:
        session_id = generate_secure_token(24)
        settings = get_settings()
        await self._client().setex(
            self._meta_key(session_id),
            settings.session_ttl_seconds,
            self._crypto.encrypt(json.dumps({"user_id": user_id})),
        )
        return session_id

    async def get_session_meta(self, session_id: str) -> dict | None:
        raw = await self._client().get(self._meta_key(session_id))
        if not raw:
            return None
        return json.loads(self._crypto.decrypt(raw))

    async def load_history(self, session_id: str) -> list[dict[str, Any]]:
        raw = await self._client().get(self._history_key(session_id))
        if not raw:
            return []
        return json.loads(self._crypto.decrypt(raw))

    async def save_message(self, session_id: str, role: str, content: str) -> None:
        history = await self.load_history(session_id)
        history.append({"role": role, "content": content})
        # Trim to avoid unbounded growth
        if len(history) > _MAX_TURNS:
            history = history[-_MAX_TURNS:]
        settings = get_settings()
        encrypted = self._crypto.encrypt(json.dumps(history))
        ttl = settings.session_ttl_seconds
        await self._client().setex(self._history_key(session_id), ttl, encrypted)
        # Refresh meta TTL on activity
        await self._client().expire(self._meta_key(session_id), ttl)

    async def append_tool_result(
        self, session_id: str, tool_use_id: str, content: str
    ) -> None:
        await self.save_message(
            session_id,
            "user",
            json.dumps([{"type": "tool_result", "tool_use_id": tool_use_id, "content": content}]),
        )

    async def delete_session(self, session_id: str) -> None:
        await self._client().delete(
            self._history_key(session_id),
            self._meta_key(session_id),
        )

    async def ping(self) -> None:
        """Lanza excepcion si Redis no responde. La usa /health/ready."""
        await self._client().ping()

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()
            self._redis = None
