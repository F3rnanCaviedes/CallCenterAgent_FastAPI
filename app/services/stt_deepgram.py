"""
Transcripción en vivo con Deepgram.

Decisión de latencia: se le pide a Deepgram que acepte mu-law 8 kHz tal cual,
que es exactamente lo que Twilio entrega. Sin resampleo ni decodificación en
medio — cada conversión de audio añade milisegundos por marco, y llegan 50
marcos por segundo.

Se usa la API WebSocket cruda en vez del SDK de Deepgram: `websockets` ya
viene instalado con uvicorn[standard], y el protocolo son cuatro mensajes.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from urllib.parse import urlencode

import websockets

from app.config import get_settings

logger = logging.getLogger(__name__)

_URL = "wss://api.deepgram.com/v1/listen"


@dataclass(slots=True)
class EventoSTT:
    """Un evento de transcripción.

    tipo:
      'parcial'      texto provisional, aun puede cambiar
      'final'        Deepgram cerro la frase; es el turno del agente
      'habla_inicio' el paciente empezo a hablar -> disparar barge-in
    """
    tipo: str
    texto: str = ""


class SesionDeepgram:
    """Un WebSocket a Deepgram por llamada telefónica."""

    def __init__(self) -> None:
        self._ws: websockets.ClientConnection | None = None

    async def __aenter__(self) -> SesionDeepgram:
        s = get_settings()
        if not s.deepgram_api_key:
            raise RuntimeError("DEEPGRAM_API_KEY no configurada")

        params = {
            "model": s.deepgram_model,
            "language": s.deepgram_language,
            # El formato de Twilio, sin tocar.
            "encoding": "mulaw",
            "sample_rate": "8000",
            "channels": "1",
            # Parciales: sin ellos no hay forma de detectar barge-in a tiempo.
            "interim_results": "true",
            "vad_events": "true",
            # Cuanto callar antes de dar la frase por cerrada. Mas bajo =
            # Sofia responde antes, pero corta a quien piensa a media frase.
            "endpointing": str(s.deepgram_endpointing_ms),
            "utterance_end_ms": str(s.deepgram_utterance_end_ms),
            "smart_format": "true",
        }
        self._ws = await websockets.connect(
            f"{_URL}?{urlencode(params)}",
            additional_headers={
                "Authorization": f"Token {s.deepgram_api_key.get_secret_value()}"
            },
        )
        logger.info("deepgram_conectado modelo=%s idioma=%s",
                    s.deepgram_model, s.deepgram_language)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._ws is None:
            return
        try:
            # CloseStream: Deepgram vacia lo que le quede y cierra limpio.
            await self._ws.send(json.dumps({"type": "CloseStream"}))
        except Exception:  # noqa: BLE001 — cerrando igual
            pass
        await self._ws.close()
        self._ws = None

    async def enviar_audio(self, marco: bytes) -> None:
        if self._ws is not None:
            await self._ws.send(marco)

    async def eventos(self) -> AsyncIterator[EventoSTT]:
        """Itera los eventos de transcripción hasta que se cierre el socket."""
        assert self._ws is not None, "usar dentro de 'async with'"
        async for crudo in self._ws:
            try:
                msg = json.loads(crudo)
            except (json.JSONDecodeError, TypeError):
                continue

            tipo = msg.get("type")

            if tipo == "SpeechStarted":
                yield EventoSTT("habla_inicio")
                continue

            if tipo != "Results":
                continue

            alternativas = msg.get("channel", {}).get("alternatives", [])
            texto = alternativas[0].get("transcript", "") if alternativas else ""
            if not texto:
                continue

            # speech_final: Deepgram detecto el fin real de la frase.
            # is_final solo significa que ese segmento ya no cambiara.
            if msg.get("speech_final"):
                yield EventoSTT("final", texto)
            elif not msg.get("is_final"):
                yield EventoSTT("parcial", texto)


async def transcribir_una_vez(marcos: list[bytes]) -> str:
    """Utilidad para pruebas: manda marcos y devuelve la primera frase final."""
    async with SesionDeepgram() as sesion:
        async def alimentar():
            for m in marcos:
                await sesion.enviar_audio(m)
                await asyncio.sleep(0.02)

        tarea = asyncio.create_task(alimentar())
        try:
            async for ev in sesion.eventos():
                if ev.tipo == "final":
                    return ev.texto
        finally:
            tarea.cancel()
    return ""
