"""
Verificación del streaming por frases.

La afirmación a probar es de latencia: el paciente debe empezar a oír la
respuesta MIENTRAS el modelo sigue generando, no después. Se comprueba
midiendo que salga audio antes de que el agente falso termine su stream.

También se comprueba que el barge-in sigue cortando el turno completo: el
stream del LLM y la síntesis a la vez.

Se ejecuta directo: `python tests/test_chat_stream.py`
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["ANTHROPIC_API_KEY"] = "test"
os.environ["DATABASE_URL"] = "postgresql://u:p@localhost:5432/d"
os.environ["API_SECRET_KEY"] = "secreto-de-prueba"
os.environ["ENCRYPTION_KEY"] = "otro-secreto"

from fastapi.testclient import TestClient  # noqa: E402

import app.api.v1.voice as voice  # noqa: E402
from app.agent.frases import extraer_frases  # noqa: E402
from app.main import app  # noqa: E402
from app.services.tts_azure import TAMANO_MARCO  # noqa: E402

# El agente falso tarda esto por frase: simula al modelo generando.
RETARDO_POR_FRASE = 0.4
FRASES = ["Claro que sí.", "Tu cita quedó para el martes.", "¿Algo más?"]


class AgenteFalso:
    """Emite frases con retardo, como haria el modelo real."""

    def __init__(self):
        self.termino_en = None

    async def chat_stream(self, session_id, user_id, user_input, db_session):
        for frase in FRASES:
            await asyncio.sleep(RETARDO_POR_FRASE)
            yield frase
        self.termino_en = time.monotonic()


class DeepgramFalso:
    """Emite una frase final del paciente, y nada mas."""

    def __init__(self):
        self.recibidos = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def enviar_audio(self, marco):
        self.recibidos += 1

    async def eventos(self):
        from app.services.stt_deepgram import EventoSTT
        await asyncio.sleep(0.05)
        yield EventoSTT("final", "quiero agendar una cita")
        while True:
            await asyncio.sleep(0.05)


async def _sintetizar_falso(texto, cliente):
    # 5 marcos = 100 ms de audio por frase.
    for _ in range(5):
        yield b"\xff" * TAMANO_MARCO


def main() -> None:
    agente = AgenteFalso()
    voice.SesionDeepgram = DeepgramFalso
    voice.sintetizar = _sintetizar_falso
    # El saludo inicial se anula: se cancela en cuanto el paciente habla y
    # sus marcos ensuciarian la medicion del primer audio de la respuesta.
    voice.Llamada.decir_en_fondo = lambda self, texto, http: None
    app.state.agent = agente

    with TestClient(app) as client:
        app.state.agent = agente  # el lifespan lo sobrescribe
        with client.websocket_connect("/v1/voice/stream") as ws:
            ws.send_text(json.dumps({
                "event": "start",
                "start": {"streamSid": "MZ1", "callSid": "CA1",
                          "customParameters": {"user_id": "paciente-1"}},
            }))

            arranque = time.monotonic()
            latencia_primer_audio = None
            recibidos = 0
            while recibidos < len(FRASES) * 5:
                msg = json.loads(ws.receive_text())
                if msg["event"] == "media":
                    if latencia_primer_audio is None:
                        latencia_primer_audio = time.monotonic() - arranque
                    recibidos += 1

            ws.send_text(json.dumps({"event": "stop"}))

    total = len(FRASES) * RETARDO_POR_FRASE
    print(f"  --  el agente tarda {total:.1f}s en generar las {len(FRASES)} frases")
    print(f"  --  primer audio a los {latencia_primer_audio:.2f}s")

    # La prueba: el audio empezo mucho antes de que el modelo terminara.
    assert latencia_primer_audio < total * 0.6, (
        f"el audio no arranco hasta {latencia_primer_audio:.2f}s de {total:.1f}s; "
        "parece que se espero la respuesta completa"
    )
    print("OK — el audio arranca con la primera frase, no al final de la respuesta")


def probar_barge_in_corta_el_stream() -> None:
    """El barge-in debe cancelar el turno entero, no solo la sintesis."""
    cancelado = asyncio.Event()

    class AgenteLento:
        async def chat_stream(self, **kw):
            try:
                for i in range(50):
                    await asyncio.sleep(0.05)
                    yield f"Frase numero {i} de la respuesta."
            finally:
                # Se ejecuta tanto con CancelledError (cancelado mientras el
                # generador esperaba) como con GeneratorExit (cancelado
                # mientras se reproducia una frase, y aclosing lo finaliza).
                # Sin aclosing este finally no corre hasta el recolector, y
                # la peticion al modelo se queda abierta.
                cancelado.set()

    async def corre():
        class WSFalso:
            async def send_text(self, _): pass

        llamada = voice.Llamada(WSFalso())
        llamada.stream_sid = "MZ1"
        import httpx
        async with httpx.AsyncClient() as http:
            llamada.hablando = asyncio.create_task(
                voice._turno(llamada, "hola", http, AgenteLento())
            )
            await asyncio.sleep(0.2)
            await llamada.callar()
        assert cancelado.is_set(), "el stream del LLM no se finalizo con el barge-in"

    # get_session_factory intentaria conectar; se sustituye por una sesion nula.
    import contextlib

    class SesionNula:
        async def __aenter__(self): return self
        async def __aexit__(self, *e): return None
        async def commit(self): pass
        async def rollback(self): pass

    voice.get_session_factory = lambda: (lambda: SesionNula())
    asyncio.run(corre())
    print("OK — barge-in finaliza el stream del LLM, no solo la sintesis")


if __name__ == "__main__":
    main()
    probar_barge_in_corta_el_stream()
