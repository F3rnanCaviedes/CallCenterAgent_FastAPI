"""
WebSocket de Twilio Media Streams.

Protocolo de Twilio: mensajes JSON con `event` en {connected, start, media,
stop}. El audio va en base64 dentro de `media.payload`, mu-law 8 kHz, marcos
de 20 ms. De vuelta se manda `media` para reproducir y `clear` para vaciar
lo que quede en el buffer de Twilio.

El barge-in es la razon de la estructura: cuando el paciente empieza a hablar
mientras Sofia responde, hay que (a) mandar `clear` para que Twilio deje de
reproducir al instante y (b) cancelar la tarea que esta generando y enviando
audio. Sin (a) el paciente sigue oyendo a Sofia unos segundos aunque paremos
de enviar, porque Twilio ya tiene audio encolado.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging

import httpx
from fastapi import (
    APIRouter,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)

from contextlib import aclosing
from xml.sax.saxutils import escape

from app.config import get_settings
from app.db.session import get_session_factory
from app.services.stt_deepgram import SesionDeepgram
from app.services.tts_azure import TTSError, sintetizar

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/voice", tags=["voice"])

# Ritmo real de reproduccion: un marco cada 20 ms. Sin esta pausa se le
# vuelca a Twilio la respuesta entera de golpe y el `clear` del barge-in
# llega tarde, porque ya tiene minutos de audio encolado.
_PAUSA_MARCO = 0.02


class Llamada:
    """Estado de una llamada. Uno por WebSocket."""

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.stream_sid: str | None = None
        self.call_sid: str | None = None
        self.user_id: str = "desconocido"
        self.session_id: str | None = None
        self.hablando: asyncio.Task | None = None

    async def decir(self, texto: str, http: httpx.AsyncClient) -> None:
        """Reproduce `texto`. Cancelable: el barge-in mata esta tarea."""
        if not texto.strip() or self.stream_sid is None:
            return
        try:
            async for marco in sintetizar(texto, http):
                await self.ws.send_text(json.dumps({
                    "event": "media",
                    "streamSid": self.stream_sid,
                    "media": {"payload": base64.b64encode(marco).decode("ascii")},
                }))
                await asyncio.sleep(_PAUSA_MARCO)
        except asyncio.CancelledError:
            raise
        except TTSError as exc:
            logger.error("tts_fallo call=%s error=%s", self.call_sid, exc)

    async def callar(self) -> None:
        """Barge-in: vacia el buffer de Twilio y corta la generacion."""
        if self.hablando and not self.hablando.done():
            self.hablando.cancel()
            try:
                await self.hablando
            except asyncio.CancelledError:
                pass
        self.hablando = None
        if self.stream_sid:
            await self.ws.send_text(json.dumps({
                "event": "clear", "streamSid": self.stream_sid,
            }))

    def decir_en_fondo(self, texto: str, http: httpx.AsyncClient) -> None:
        self.hablando = asyncio.create_task(self.decir(texto, http))


async def _turno(llamada: Llamada, texto_usuario: str, http: httpx.AsyncClient,
                 agente) -> None:
    """Turno completo: pregunta del paciente -> respuesta hablada.

    El agente emite frases segun las genera el modelo y cada una se sintetiza
    en cuanto llega. El paciente empieza a oir la respuesta mientras el modelo
    todavia escribe el resto, en vez de esperar a que termine.

    Toda la corrutina es cancelable: el barge-in la mata y con ella se cortan
    a la vez el stream del LLM y la sintesis en curso.
    """
    async with get_session_factory()() as db:
        try:
            # aclosing: sin esto, si el barge-in cancela mientras se esta
            # reproduciendo una frase, el generador queda suspendido en su
            # `yield` y nadie lo finaliza hasta que pase el recolector. La
            # peticion HTTP al modelo sigue abierta mientras tanto, generando
            # tokens que ya nadie va a oir. Con barge-in frecuente eso son
            # conexiones y gasto acumulandose durante toda la llamada.
            async with aclosing(agente.chat_stream(
                session_id=llamada.session_id or f"voz_{llamada.call_sid}",
                user_id=llamada.user_id,
                user_input=texto_usuario,
                db_session=db,
            )) as respuesta:
                async for frase in respuesta:
                    await llamada.decir(frase, http)
            await db.commit()
        except asyncio.CancelledError:
            # Barge-in: la sesion se deshace sin commit a medias.
            await db.rollback()
            raise
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            logger.error("agente_fallo call=%s error=%s", llamada.call_sid, exc)
            await llamada.decir("Disculpa, tuve un problema. ¿Puedes repetirlo?", http)


def _validar_firma_twilio(request: Request, cuerpo: dict) -> None:
    """Comprueba X-Twilio-Signature.

    Este webhook es publico por necesidad — Twilio tiene que alcanzarlo — asi
    que sin la firma cualquiera puede provocar llamadas del agente. El token
    de auth de Twilio es la clave con la que ellos firman.
    """
    from twilio.request_validator import RequestValidator

    s = get_settings()
    if not s.twilio_auth_token:
        raise HTTPException(status_code=503, detail="Twilio no configurado")

    firma = request.headers.get("X-Twilio-Signature", "")
    validador = RequestValidator(s.twilio_auth_token.get_secret_value())
    # La URL publica debe coincidir byte a byte con la registrada en Twilio;
    # detras de un proxy, str(request.url) puede traer http y el esquema
    # equivocado invalida la firma. Por eso se configura explicita.
    url = f"{s.public_base_url}{request.url.path}"
    if not validador.validate(url, cuerpo, firma):
        logger.warning("twilio_firma_invalida url=%s", url)
        raise HTTPException(status_code=403, detail="Firma inválida")


@router.post("/incoming")
async def llamada_entrante(request: Request) -> Response:
    """Webhook de Twilio: responde el TwiML que abre el Media Stream.

    Sirve igual para una llamada que entra por un numero de Twilio y para una
    que llega del PBX por el trunk SIP — en ambos casos Twilio golpea aqui.
    """
    formulario = dict((await request.form()))
    _validar_firma_twilio(request, formulario)

    s = get_settings()
    ws_url = s.public_base_url.replace("https://", "wss://").replace("http://", "ws://")
    # El user_id viaja como <Parameter>: llega en customParameters del evento
    # `start`, que es donde lo recoge el WebSocket.
    user_id = formulario.get("From", "desconocido")

    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Connect>"
        f'<Stream url="{ws_url}/v1/voice/stream">'
        f'<Parameter name="user_id" value="{escape(user_id)}"/>'
        "</Stream>"
        "</Connect></Response>"
    )
    return Response(content=twiml, media_type="application/xml")


@router.websocket("/stream")
async def media_stream(ws: WebSocket) -> None:
    await ws.accept()
    llamada = Llamada(ws)
    agente = ws.app.state.agent
    sesiones = ws.app.state.sessions
    # Referencias vivas: una tarea sin referencia puede recogerla el GC.
    metricas: list[asyncio.Task] = []

    # Un solo cliente HTTP para toda la llamada: la conexion TLS con Azure se
    # reutiliza entre frases en vez de renegociar en cada una.
    async with httpx.AsyncClient(timeout=10.0) as http:
        try:
            async with SesionDeepgram() as stt:

                async def bombear_transcripcion() -> None:
                    """Deepgram -> agente. Corre en paralelo al audio entrante."""
                    async for ev in stt.eventos():
                        if ev.tipo == "habla_inicio":
                            await llamada.callar()
                        elif ev.tipo == "final":
                            logger.info("paciente_dijo call=%s texto=%r",
                                        llamada.call_sid, ev.texto)
                            await llamada.callar()
                            llamada.hablando = asyncio.create_task(
                                _turno(llamada, ev.texto, http, agente)
                            )

                tarea_stt = asyncio.create_task(bombear_transcripcion())

                try:
                    async for crudo in ws.iter_text():
                        msg = json.loads(crudo)
                        evento = msg.get("event")

                        if evento == "start":
                            inicio = msg.get("start", {})
                            llamada.stream_sid = inicio.get("streamSid")
                            llamada.call_sid = inicio.get("callSid")
                            # customParameters llega desde el TwiML <Parameter>:
                            # ahi es donde el portal inyecta a que paciente
                            # corresponde la llamada.
                            params = inicio.get("customParameters") or {}
                            llamada.user_id = params.get("user_id", "desconocido")
                            logger.info("llamada_inicio call=%s user=%s",
                                        llamada.call_sid, llamada.user_id)
                            llamada.decir_en_fondo(
                                "Hola, soy Sofía. ¿En qué te puedo ayudar?", http
                            )
                            # En tarea aparte: con Redis lento el saludo no espera.
                            metricas.append(asyncio.create_task(
                                sesiones.registrar_llamada("inicio", llamada.call_sid)
                            ))

                        elif evento == "media":
                            await stt.enviar_audio(
                                base64.b64decode(msg["media"]["payload"])
                            )

                        elif evento == "stop":
                            logger.info("llamada_fin call=%s", llamada.call_sid)
                            break

                finally:
                    tarea_stt.cancel()
                    await llamada.callar()

        except WebSocketDisconnect:
            logger.info("ws_desconectado call=%s", llamada.call_sid)
        except Exception as exc:  # noqa: BLE001
            logger.error("ws_error call=%s error=%s", llamada.call_sid, exc)
            await sesiones.registrar_llamada("error", llamada.call_sid)
        finally:
            if llamada.stream_sid is not None:
                await asyncio.gather(*metricas)
                await sesiones.registrar_llamada("fin", llamada.call_sid)
