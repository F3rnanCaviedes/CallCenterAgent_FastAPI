"""
Verificación del endpoint de emisión de tokens.

Se ejecuta directo: `python tests/test_auth_token.py`
No necesita Postgres ni Redis — sólo toca /v1/auth/token y /health.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CLAVE = "clave-de-prueba-no-usar-en-produccion"

os.environ["ANTHROPIC_API_KEY"] = "test"
os.environ["DATABASE_URL"] = "postgresql://u:p@localhost:5432/d"
os.environ["API_SECRET_KEY"] = "secreto-de-prueba-para-firmar-jwt"
os.environ["ENCRYPTION_KEY"] = "otro-secreto-de-prueba"
os.environ["CLIENT_API_KEYS"] = f'["{CLAVE}"]'
os.environ["APP_ENV"] = "development"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.security.auth import verify_api_token  # noqa: E402


def main() -> None:
    # `with`: corre el lifespan, que es lo que puebla app.state.
    # Sin el context manager los endpoints que usan app.state.crypto
    # revientan con AttributeError y el test no reflejaria produccion.
    with TestClient(app) as client:
        _comprobar(client)


def _comprobar(client) -> None:

    assert client.get("/health").status_code == 200

    # Clave válida -> token utilizable, con el sub pedido.
    r = client.post(
        "/v1/auth/token",
        json={"user_id": "paciente-123"},
        headers={"X-API-Key": CLAVE},
    )
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["token_type"] == "Bearer"
    assert cuerpo["expires_in"] == 3600
    claims = verify_api_token(cuerpo["access_token"])
    assert claims["sub"] == "paciente-123", claims

    # Clave incorrecta, clave ausente y user_id inválido: los tres rechazados.
    assert client.post(
        "/v1/auth/token",
        json={"user_id": "paciente-123"},
        headers={"X-API-Key": "clave-equivocada"},
    ).status_code == 401
    assert client.post(
        "/v1/auth/token", json={"user_id": "paciente-123"}
    ).status_code == 401
    assert client.post(
        "/v1/auth/token",
        json={"user_id": "malo; DROP TABLE"},
        headers={"X-API-Key": CLAVE},
    ).status_code == 422

    # El token emitido abre de verdad un endpoint protegido: sin él, 401.
    assert client.get("/v1/appointments/paciente-123").status_code == 401

    # Sin claves configuradas el endpoint cierra (503), no abre.
    get_settings.cache_clear()
    os.environ["CLIENT_API_KEYS"] = "[]"
    try:
        r = TestClient(app).post(
            "/v1/auth/token",
            json={"user_id": "paciente-123"},
            headers={"X-API-Key": CLAVE},
        )
        assert r.status_code == 503, r.status_code
    finally:
        os.environ["CLIENT_API_KEYS"] = f'["{CLAVE}"]'
        get_settings.cache_clear()

    # El limitador global aplica de verdad: pasado el tope llega el 429.
    # Sin SlowAPIMiddleware este bucle devolvería 200 indefinidamente.
    tope = get_settings().rate_limit_per_minute
    cliente_limpio = TestClient(app)
    codigos = [
        cliente_limpio.post(
            "/v1/auth/token",
            json={"user_id": "paciente-123"},
            headers={"X-API-Key": CLAVE},
        ).status_code
        for _ in range(tope + 5)
    ]
    assert 429 in codigos, f"el limitador nunca disparo: {sorted(set(codigos))}"

    print("OK — /v1/auth/token: emite, valida clave, valida user_id, cierra sin claves")
    print("OK — limitador global activo (429 tras %d peticiones/min)" % tope)


if __name__ == "__main__":
    main()
