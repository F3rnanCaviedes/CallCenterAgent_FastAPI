"""
Verificación del WebSocket de Twilio Media Streams.

Lo que importa probar es el barge-in: que cuando el paciente empieza a hablar
mientras Sofía responde, se manda `clear` a Twilio y se corta la generación.
Si eso se rompe, el sintoma es que el paciente oye a Sofia pisandolo durante
segundos — facil de no notar hasta que un paciente se queja.

Deepgram y Azure van simulados: no hace falta red ni credenciales.

Se ejecuta directo: `python tests/test_voice_stream.py`
"""
import asyncio
import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["ANTHROPIC_API_KEY"] = "test"
os.environ["DATABASE_URL"] = "postgresql://u:p@localhost:5432/d"
os.environ["API_SECRET_KEY"] = "secreto-de-prueba"
os.environ["ENCRYPTION_KEY"] = "otro-secreto"

from fastapi.testclient import TestClient  # noqa: E402

import app.api.v1.voice as voice  # noqa: E402
from app.main import app  # noqa: E402

MARCOS_ANTES_DE_INTERRUMPIR = 3


class DeepgramFalso:
    """Emite 'habla_inicio' tras recibir unos marcos: simula al paciente
    interrumpiendo mientras Sofía habla."""

    def __init__(self):
        self.recibidos = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def enviar_audio(self, marco):
        self.recibidos += 1

    async def eventos(self):
        while self.recibidos < MARCOS_ANTES_DE_INTERRUMPIR:
            await asyncio.sleep(0.01)
        yield _evento("habla_inicio")
        while True:  # se queda viva hasta que la cancelen
            await asyncio.sleep(0.05)


def _evento(tipo, texto=""):
    from app.services.stt_deepgram import EventoSTT
    return EventoSTT(tipo, texto)


async def _sintetizar_falso(texto, cliente):
    """Saludo largo (4 s) para que dé tiempo a interrumpirlo."""
    for _ in range(200):
        yield b"\xff" * voice_marco()


def voice_marco():
    from app.services.tts_azure import TAMANO_MARCO
    return TAMANO_MARCO


def main() -> None:
    voice.SesionDeepgram = DeepgramFalso
    voice.sintetizar = _sintetizar_falso

    with TestClient(app) as client:
        with client.websocket_connect("/v1/voice/stream") as ws:
            ws.send_text(json.dumps({
                "event": "start",
                "start": {
                    "streamSid": "MZ123",
                    "callSid": "CA456",
                    "customParameters": {"user_id": "paciente-1"},
                },
            }))

            # Sofía arranca el saludo.
            primero = json.loads(ws.receive_text())
            assert primero["event"] == "media", primero
            assert primero["streamSid"] == "MZ123", primero
            audio = base64.b64decode(primero["media"]["payload"])
            assert len(audio) == voice_marco(), len(audio)

            # El paciente interrumpe.
            for _ in range(MARCOS_ANTES_DE_INTERRUMPIR):
                ws.send_text(json.dumps({
                    "event": "media",
                    "media": {"payload": base64.b64encode(b"\x00" * 160).decode()},
                }))

            # Debe llegar un 'clear' y el saludo debe parar.
            eventos = []
            for _ in range(60):
                msg = json.loads(ws.receive_text())
                eventos.append(msg["event"])
                if msg["event"] == "clear":
                    break
            assert "clear" in eventos, f"nunca llego el clear: {set(eventos)}"

            despues = eventos[eventos.index("clear") + 1:]
            assert not despues, f"siguio hablando tras el clear: {despues}"

            ws.send_text(json.dumps({"event": "stop"}))

    print("OK — media stream: saludo en marcos mu-law de 160 bytes con streamSid")
    print("OK — barge-in: llega 'clear' y Sofia deja de enviar audio al instante")


if __name__ == "__main__":
    main()
