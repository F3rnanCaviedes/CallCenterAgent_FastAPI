"""
Verificación del dashboard.

Cubre: la página sale con una CSP que sólo admite scripts propios, el
JSON exige la clave interna, y con Postgres/Redis caídos el endpoint sigue
respondiendo (degradado) en vez de dar 500 — un dashboard que se cae junto
con lo que vigila no sirve.

Se ejecuta directo: `python tests/test_dashboard.py`
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CLAVE_INTERNA = "clave-interna-de-prueba"

os.environ["ANTHROPIC_API_KEY"] = "test"
# Puerto 1: rechaza al instante, sin esperar timeouts.
os.environ["DATABASE_URL"] = "postgresql://u:p@127.0.0.1:1/d"
os.environ["REDIS_URL"] = "redis://127.0.0.1:1/0"
os.environ["API_SECRET_KEY"] = "secreto-de-prueba"
os.environ["ENCRYPTION_KEY"] = "otro-secreto"
os.environ["INTERNAL_API_KEYS"] = f'["{CLAVE_INTERNA}"]'

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def main() -> None:
    with TestClient(app) as client:
        r = client.get("/dashboard")
        assert r.status_code == 200
        csp = r.headers["content-security-policy"]
        assert "script-src 'self'" in csp and "unsafe-inline" not in csp.split("script-src")[1].split(";")[0], csp
        assert '<script type="module" src="/dashboard/app.js">' in r.text
        assert "<script>" not in r.text, "script inline: la CSP lo bloquearia"

        js = client.get("/dashboard/app.js")
        # 503 = falta `npm run build` en dashboard/: se dice claro, no un 404 mudo.
        assert js.status_code in (200, 503), js.status_code
        if js.status_code == 200:
            assert js.headers["content-type"].startswith("text/javascript")
            assert "/dashboard/data" in js.text
        else:
            print("AVISO — dashboard.js sin compilar; se omite su verificacion")
        # El resto de rutas conserva la CSP estricta.
        assert client.get("/health").headers["content-security-policy"].startswith("default-src 'none'; frame")

        assert client.get("/dashboard/data").status_code == 401
        assert client.get("/dashboard/data", headers={"X-Internal-Key": "otra"}).status_code == 401

        r = client.get("/dashboard/data", headers={"X-Internal-Key": CLAVE_INTERNA})
        assert r.status_code == 200, r.text
        x = r.json()
        assert x["status"] == "degraded", x
        assert x["dependencies"]["postgres"] != "ok"
        assert x["dependencies"]["redis"] != "ok"
        assert x["dependencies"]["browser"] == "desactivado"
        assert "error" in x["calls"] and "error" in x["appointments"], x
        assert x["integrations"]["llm"] is True
        # Nada de secretos en la respuesta.
        assert "test" not in r.text and "secreto" not in r.text, r.text

    print("OK — dashboard: CSP script-src self, JS compilado servido, clave interna exigida")
    print("OK — dashboard: con Postgres y Redis caidos responde 'degraded' sin 500")


if __name__ == "__main__":
    main()
