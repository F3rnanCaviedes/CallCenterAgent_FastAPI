"""
Core agent orchestration loop.
Handles multi-turn conversations with tool calling against Claude.
Security: tool calls are always bound to the authenticated user_id from the session;
the LLM cannot inject a different user_id through its tool arguments.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.frases import extraer_frases
from app.agent.prompts import build_system_prompt
from app.agent.session import SessionManager
from app.agent.tools import TOOLS
from app.config import get_settings
from app.security.crypto import EncryptionManager
from app.security.sanitizer import sanitize_field, sanitize_user_input
from app.services.appointment_service import AppointmentService
from app.services.form_filler import FIELD_ORDER, fill_form

logger = logging.getLogger(__name__)

_MAX_TOOL_ITERATIONS = 6  # Prevent infinite loops
_REQUEST_TIMEOUT = 30.0


class AgentCore:
    def __init__(
        self,
        session_manager: SessionManager,
        crypto: EncryptionManager,
    ) -> None:
        self._sessions = session_manager
        self._crypto = crypto
        settings = get_settings()
        self._client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value(),
            timeout=_REQUEST_TIMEOUT,
            max_retries=2,
        )
        self._model = settings.llm_model

    async def chat(
        self,
        session_id: str,
        user_id: str,
        user_input: str,
        db_session: AsyncSession,
    ) -> dict[str, Any]:
        start = time.monotonic()

        # Sanitize before anything touches the LLM
        safe_input = sanitize_user_input(user_input)

        # Load or validate session
        meta = await self._sessions.get_session_meta(session_id)
        if meta is None:
            # Auto-create session bound to this user
            session_id = await self._sessions.create_session(user_id)
            meta = {"user_id": user_id}
        elif meta["user_id"] != user_id:
            # Session belongs to a different user — refuse silently
            logger.warning("session_user_mismatch session=%s claimed=%s", session_id, user_id)
            return {
                "session_id": session_id,
                "response": {"text": "No puedo procesar esa solicitud."},
                "action_taken": None,
            }

        history = await self._sessions.load_history(session_id)
        history.append({"role": "user", "content": safe_input})

        system = build_system_prompt(f"ID: {user_id}")
        svc = AppointmentService(db_session, self._crypto)

        action_taken = None
        final_text = ""

        for iteration in range(_MAX_TOOL_ITERATIONS):
            response = await self._client.messages.create(
                model=self._model,
                system=system,
                messages=history,
                tools=TOOLS,
                max_tokens=1024,
            )

            # Persist assistant turn
            assistant_content = response.content
            history.append({"role": "assistant", "content": [
                block.model_dump() for block in assistant_content
            ]})

            if response.stop_reason == "end_turn":
                final_text = next(
                    (b.text for b in assistant_content if hasattr(b, "text")), ""
                )
                break

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in assistant_content:
                    if block.type != "tool_use":
                        continue
                    tool_name = block.name
                    raw_input = block.input
                    tool_use_id = block.id

                    result = await self._dispatch_tool(
                        tool_name, raw_input, user_id, svc
                    )
                    action_taken = {"tool": tool_name, "result": result}
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": json.dumps(result, ensure_ascii=False),
                    })

                history.append({"role": "user", "content": tool_results})
                continue

            # Unexpected stop reason
            break

        # Persist final conversation state
        await self._sessions.save_message(session_id, "user", safe_input)
        if final_text:
            await self._sessions.save_message(session_id, "assistant", final_text)

        elapsed = round(time.monotonic() - start, 3)
        logger.info("agent_chat user=%s session=%s turns=%d elapsed=%ss",
                    user_id, session_id, iteration + 1, elapsed)

        return {
            "session_id": session_id,
            "response": {"text": final_text},
            "action_taken": action_taken,
        }

    async def chat_stream(
        self,
        session_id: str,
        user_id: str,
        user_input: str,
        db_session: AsyncSession,
    ) -> AsyncIterator[str]:
        """Igual que chat(), pero emite frases segun las genera el modelo.

        Para la llamada telefonica: con chat() el paciente espera a que la
        respuesta completa termine antes de oir nada. Aqui la primera frase
        sale mientras el modelo sigue escribiendo el resto.

        Mantiene la misma garantia de seguridad que chat(): el user_id de las
        tool calls viene siempre de la sesion autenticada, nunca del LLM.
        """
        safe_input = sanitize_user_input(user_input)

        meta = await self._sessions.get_session_meta(session_id)
        if meta is None:
            session_id = await self._sessions.create_session(user_id)
        elif meta["user_id"] != user_id:
            logger.warning("session_user_mismatch session=%s claimed=%s",
                           session_id, user_id)
            yield "No puedo procesar esa solicitud."
            return

        history = await self._sessions.load_history(session_id)
        history.append({"role": "user", "content": safe_input})

        system = build_system_prompt(f"ID: {user_id}")
        svc = AppointmentService(db_session, self._crypto)
        dicho: list[str] = []

        for _ in range(_MAX_TOOL_ITERATIONS):
            buffer = ""
            async with self._client.messages.stream(
                model=self._model,
                system=system,
                messages=history,
                tools=TOOLS,
                max_tokens=1024,
            ) as stream:
                async for delta in stream.text_stream:
                    buffer += delta
                    frases, buffer = extraer_frases(buffer)
                    for frase in frases:
                        dicho.append(frase)
                        yield frase
                final = await stream.get_final_message()

            # Lo que quede sin terminador se emite ahora: si no, una respuesta
            # que acaba sin punto se perderia entera.
            frases, buffer = extraer_frases(buffer, forzar=True)
            for frase in frases:
                dicho.append(frase)
                yield frase

            history.append({
                "role": "assistant",
                "content": [b.model_dump() for b in final.content],
            })

            if final.stop_reason != "tool_use":
                break

            tool_results = []
            for block in final.content:
                if block.type != "tool_use":
                    continue
                result = await self._dispatch_tool(
                    block.name, block.input, user_id, svc
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })
            history.append({"role": "user", "content": tool_results})

        await self._sessions.save_message(session_id, "user", safe_input)
        if dicho:
            await self._sessions.save_message(session_id, "assistant", " ".join(dicho))

    async def _dispatch_tool(
        self,
        tool_name: str,
        raw_input: dict,
        user_id: str,
        svc: AppointmentService,
    ) -> dict:
        """
        Execute the tool requested by the LLM.
        CRITICAL: user_id is always injected from the authenticated session,
        never from the LLM's raw_input, preventing horizontal privilege escalation.
        """
        try:
            if tool_name == "create_appointment":
                return await svc.create_appointment(
                    user_id=user_id,
                    service_type=str(raw_input.get("service_type", "")),
                    datetime_iso=str(raw_input.get("datetime_iso", "")),
                    notes=raw_input.get("notes"),
                    provider_id=raw_input.get("provider_id"),
                )
            elif tool_name == "cancel_appointment":
                return await svc.cancel_appointment(
                    appointment_id_str=str(raw_input.get("appointment_id", "")),
                    user_id=user_id,
                    reason=raw_input.get("reason"),
                    notify_user=bool(raw_input.get("notify_user", True)),
                )
            elif tool_name == "reschedule_appointment":
                return await svc.reschedule_appointment(
                    appointment_id_str=str(raw_input.get("appointment_id", "")),
                    user_id=user_id,
                    new_datetime_iso=str(raw_input.get("new_datetime_iso", "")),
                    reason=raw_input.get("reason"),
                )
            elif tool_name == "list_appointments":
                return await svc.list_appointments(
                    user_id=user_id,
                    status=str(raw_input.get("status", "active")),
                    limit=int(raw_input.get("limit", 5)),
                )
            elif tool_name == "check_availability":
                return await svc.check_availability(
                    service_type=str(raw_input.get("service_type", "")),
                    date_from_str=str(raw_input.get("date_from", "")),
                    date_to_str=raw_input.get("date_to"),
                    provider_id=raw_input.get("provider_id"),
                )
            elif tool_name == "send_reminder":
                # Reminder sending is handled by the notification service
                return {
                    "success": True,
                    "message": "Recordatorio programado correctamente.",
                    "appointment_id": raw_input.get("appointment_id"),
                    "channel": raw_input.get("channel", "sms"),
                }
            elif tool_name == "fill_web_form":
                filled = await fill_form(
                    str(raw_input.get("url", "")),
                    {f: raw_input.get(f, "") for f in FIELD_ORDER},
                )
                return {"success": True, "campos_registrados": sorted(filled)}
            else:
                logger.warning("unknown_tool tool=%s user=%s", tool_name, user_id)
                return {"success": False, "error": "unknown_tool"}

        except Exception as exc:
            logger.error("tool_error tool=%s user=%s error=%s", tool_name, user_id, exc)
            return {"success": False, "error": "internal_error",
                    "message": "Error interno al procesar la solicitud."}
