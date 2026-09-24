"""
Verificación del disparo de recordatorios.

Cubre lo que no necesita Postgres: el control de acceso del endpoint y el
despacho por canal. El bucle sobre la base de datos se prueba en integración
con una Postgres real (pendiente).

Se ejecuta directo: `python tests/test_reminders.py`
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CLAVE_INTERNA = "clave-interna-de-prueba"

os.environ["ANTHROPIC_API_KEY"] = "test"
os.environ["DATABASE_URL"] = "postgresql://u:p@localhost:5432/d"
os.environ["API_SECRET_KEY"] = "secreto-de-prueba-para-firmar-jwt"
os.environ["ENCRYPTION_KEY"] = "otro-secreto-de-prueba"
os.environ["CLIENT_API_KEYS"] = '["clave-cliente"]'
os.environ["INTERNAL_API_KEYS"] = f'["{CLAVE_INTERNA}"]'

from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.security.auth import create_api_token  # noqa: E402
from app.services.notifications import (  # noqa: E402
    NotificationError,
    enviar_recordatorio,
)


def probar_control_de_acceso(client) -> None:
    # Sin clave interna: rechazado.
    assert client.post("/v1/reminders/trigger").status_code == 401

    # Clave equivocada: rechazado.
    assert client.post(
        "/v1/reminders/trigger", headers={"X-Internal-Key": "otra"}
    ).status_code == 401

    # Un JWT de paciente válido ya NO sirve: era el agujero original, donde
    # cualquier usuario autenticado podia disparar el envio masivo.
    token = create_api_token("paciente-cualquiera")
    assert client.post(
        "/v1/reminders/trigger", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 401

    # Clave de cliente (la que emite tokens) tampoco vale para esto.
    assert client.post(
        "/v1/reminders/trigger", headers={"X-Internal-Key": "clave-cliente"}
    ).status_code == 401


def probar_sin_configurar(client_factory) -> None:
    get_settings.cache_clear()
    os.environ["INTERNAL_API_KEYS"] = "[]"
    try:
        with client_factory() as c:
            r = c.post(
                "/v1/reminders/trigger", headers={"X-Internal-Key": CLAVE_INTERNA}
            )
            assert r.status_code == 503, r.status_code
    finally:
        os.environ["INTERNAL_API_KEYS"] = f'["{CLAVE_INTERNA}"]'
        get_settings.cache_clear()


def probar_despacho() -> None:
    async def corre():
        # Sin dato de contacto: error, nunca exito silencioso.
        for canal in ("sms", "email", "whatsapp"):
            try:
                await enviar_recordatorio(canal, None, "hola", "asunto")
                raise AssertionError(f"{canal} sin destino deberia fallar")
            except NotificationError:
                pass

        # Canal desconocido y canal aun no implementado: ambos fallan claro.
        for canal in ("voice", "paloma-mensajera"):
            try:
                await enviar_recordatorio(canal, "+573001234567", "hola", "asunto")
                raise AssertionError(f"{canal} deberia fallar")
            except NotificationError:
                pass

        # Twilio sin credenciales: falla en vez de simular envio.
        try:
            await enviar_recordatorio("sms", "+573001234567", "hola", "asunto")
            raise AssertionError("sms sin credenciales deberia fallar")
        except NotificationError:
            pass

    asyncio.run(corre())


def main() -> None:
    with TestClient(app) as client:
        probar_control_de_acceso(client)
    probar_sin_configurar(lambda: TestClient(app))
    probar_despacho()
    print("OK — /v1/reminders/trigger: solo clave interna (JWT de paciente rechazado)")
    print("OK — despacho: sin destino, canal invalido y sin credenciales fallan explicito")


if __name__ == "__main__":
    main()
