"""
Envío de recordatorios por SMS, WhatsApp y correo.

Twilio y SendGrid exponen clientes síncronos. Llamarlos directo desde una
corrutina bloquearía el event loop durante toda la petición HTTP de salida,
así que cada envío va a un hilo con asyncio.to_thread.
"""
from __future__ import annotations

import asyncio
import logging

from app.config import get_settings

logger = logging.getLogger(__name__)


class NotificationError(RuntimeError):
    """Fallo de envío. El llamante marca el recordatorio como 'failed'."""


def _enviar_twilio_sync(destino: str, cuerpo: str, whatsapp: bool) -> str:
    from twilio.rest import Client

    s = get_settings()
    if not (s.twilio_account_sid and s.twilio_auth_token and s.twilio_phone_number):
        raise NotificationError("Twilio no configurado")

    cliente = Client(
        s.twilio_account_sid.get_secret_value(),
        s.twilio_auth_token.get_secret_value(),
    )
    origen = s.twilio_phone_number
    if whatsapp:
        origen = f"whatsapp:{origen}"
        destino = f"whatsapp:{destino}"

    msg = cliente.messages.create(to=destino, from_=origen, body=cuerpo)
    return msg.sid


def _enviar_sendgrid_sync(destino: str, asunto: str, cuerpo: str) -> str:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail

    s = get_settings()
    if not (s.sendgrid_api_key and s.notification_email_from):
        raise NotificationError("SendGrid no configurado")

    mail = Mail(
        from_email=s.notification_email_from,
        to_emails=destino,
        subject=asunto,
        plain_text_content=cuerpo,
    )
    resp = SendGridAPIClient(s.sendgrid_api_key.get_secret_value()).send(mail)
    if resp.status_code >= 300:
        raise NotificationError(f"SendGrid respondio {resp.status_code}")
    return resp.headers.get("X-Message-Id", "sin-id")


async def enviar_recordatorio(
    canal: str, destino: str | None, mensaje: str, asunto: str
) -> str:
    """Envía por el canal pedido. Devuelve el id del proveedor.

    Lanza NotificationError si el canal no se puede atender; nunca devuelve
    éxito silencioso, porque un recordatorio marcado como enviado sin haberse
    enviado es peor que uno fallido: nadie va a revisarlo.
    """
    if not destino:
        raise NotificationError(f"el usuario no tiene dato de contacto para '{canal}'")

    if canal in ("sms", "whatsapp"):
        return await asyncio.to_thread(
            _enviar_twilio_sync, destino, mensaje, canal == "whatsapp"
        )
    if canal == "email":
        return await asyncio.to_thread(_enviar_sendgrid_sync, destino, asunto, mensaje)
    if canal == "voice":
        # Pendiente: es una llamada saliente de Twilio, parte del trabajo de
        # voz en tiempo real. Falla explicito para que quede contabilizado.
        raise NotificationError("canal 'voice' aun no implementado")

    raise NotificationError(f"canal desconocido: {canal}")
