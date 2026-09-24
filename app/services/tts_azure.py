"""
Síntesis de voz con Azure, en streaming.

Dos decisiones de latencia:

1. Se pide `raw-8khz-8bit-mono-mulaw`, que esta en la lista de formatos de
   streaming de Azure y es exactamente lo que consume Twilio Media Streams.
   Cero resampleo, cero codificacion mu-law propia.

2. Se autentica con Ocp-Apim-Subscription-Key en vez del flujo de token STS.
   El token ahorra cabecera pero cuesta una peticion HTTP extra cada 9
   minutos, y la primera llamada de cada ventana la paga el paciente.

Se usa httpx (ya instalado) en vez del SDK de Azure Speech, que arrastra
binarios nativos y no aporta nada a un POST con streaming.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from xml.sax.saxutils import escape

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# Twilio consume marcos de 20 ms: 8000 muestras/s * 0.020 s * 1 byte = 160 bytes.
TAMANO_MARCO = 160


class TTSError(RuntimeError):
    pass


def _ssml(texto: str, voz: str, idioma: str) -> str:
    # escape(): el texto viene del LLM y podria traer < o &, que romperian
    # el SSML y harian que Azure devuelva 400 a mitad de conversacion.
    return (
        f"<speak version='1.0' xml:lang='{idioma}'>"
        f"<voice name='{voz}'>{escape(texto)}</voice>"
        f"</speak>"
    )


async def sintetizar(
    texto: str, cliente: httpx.AsyncClient
) -> AsyncIterator[bytes]:
    """Genera marcos mu-law de 20 ms listos para Twilio.

    Recibe el cliente httpx desde fuera para reutilizar la conexion TLS entre
    frases: renegociar TLS en cada frase añade cientos de milisegundos.
    """
    s = get_settings()
    if not (s.azure_tts_key and s.azure_tts_region):
        raise TTSError("Azure TTS no configurado")

    url = (
        f"https://{s.azure_tts_region}.tts.speech.microsoft.com"
        f"/cognitiveservices/v1"
    )
    cabeceras = {
        "Ocp-Apim-Subscription-Key": s.azure_tts_key.get_secret_value(),
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "raw-8khz-8bit-mono-mulaw",
        "User-Agent": "SofiaAgent",
    }
    cuerpo = _ssml(texto, s.azure_tts_voice, s.azure_tts_language)

    resto = b""
    async with cliente.stream("POST", url, headers=cabeceras, content=cuerpo) as resp:
        if resp.status_code != 200:
            detalle = (await resp.aread())[:200]
            raise TTSError(f"Azure TTS respondio {resp.status_code}: {detalle!r}")

        async for trozo in resp.aiter_bytes():
            resto += trozo
            # Trocear en marcos exactos: Twilio acepta tamaños variables, pero
            # marcos irregulares producen saltos audibles en la reproduccion.
            while len(resto) >= TAMANO_MARCO:
                yield resto[:TAMANO_MARCO]
                resto = resto[TAMANO_MARCO:]

    if resto:
        # Ultimo marco corto: se rellena con silencio mu-law (0xFF).
        yield resto + b"\xff" * (TAMANO_MARCO - len(resto))
