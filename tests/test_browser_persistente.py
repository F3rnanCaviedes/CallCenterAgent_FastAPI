"""
Verificación del navegador Playwright compartido.

Dos afirmaciones que probar:

1. Latencia: reutilizar el navegador evita el arranque de Chromium en cada
   llenado. Es la razón de existir del módulo, así que se mide.
2. Aislamiento: cada llenado va en su propio contexto, para que la sesión
   del portal de un paciente no quede disponible para el siguiente.

Se ejecuta directo: `python tests/test_browser_persistente.py`
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["ANTHROPIC_API_KEY"] = "test"
os.environ["DATABASE_URL"] = "postgresql://u:p@localhost:5432/d"
os.environ["API_SECRET_KEY"] = "secreto-de-prueba"
os.environ["ENCRYPTION_KEY"] = "otro-secreto"

from playwright.async_api import async_playwright  # noqa: E402

from app.services.browser import NavegadorCompartido  # noqa: E402

FORMULARIO = "<form><input name='nombre'><input name='correo'></form>"


async def _lanzar_desde_cero() -> float:
    """Lo que costaba antes: Chromium nuevo en cada llenado."""
    inicio = time.monotonic()
    async with async_playwright() as pw:
        navegador = await pw.chromium.launch(headless=True)
        pagina = await navegador.new_page()
        await pagina.set_content(FORMULARIO)
        await pagina.fill("[name='nombre']", "Ana Muñoz")
        await navegador.close()
    return time.monotonic() - inicio


async def _con_compartido(nav: NavegadorCompartido) -> float:
    inicio = time.monotonic()
    async with nav.pagina() as pagina:
        await pagina.set_content(FORMULARIO)
        await pagina.fill("[name='nombre']", "Ana Muñoz")
    return time.monotonic() - inicio


async def main() -> None:
    frio = await _lanzar_desde_cero()
    print(f"  --  lanzando Chromium cada vez: {frio*1000:.0f} ms por llenado")

    nav = NavegadorCompartido()
    await nav.iniciar()
    try:
        # Varias veces: la primera podria beneficiarse de cache del sistema.
        tiempos = [await _con_compartido(nav) for _ in range(3)]
        caliente = sum(tiempos) / len(tiempos)
        print(f"  --  navegador compartido: {caliente*1000:.0f} ms por llenado")
        print(f"  --  ahorro por cita registrada: {(frio-caliente)*1000:.0f} ms")

        assert caliente < frio / 2, (
            f"el compartido ({caliente*1000:.0f} ms) no mejora claramente "
            f"al arranque en frio ({frio*1000:.0f} ms)"
        )
        print("OK — reutilizar el navegador recorta el llenado a menos de la mitad")

        # Aislamiento: la cookie de un paciente no puede verse en el siguiente.
        async with nav.pagina() as p:
            await p.context.add_cookies([{
                "name": "sesion_portal", "value": "paciente-A",
                "url": "https://portal.clinica.com",
            }])
        async with nav.pagina() as p:
            fugadas = await p.context.cookies("https://portal.clinica.com")
            assert fugadas == [], f"sesion filtrada al siguiente paciente: {fugadas}"
        print("OK — la sesión del portal no se filtra entre pacientes")

        # Recuperacion: si Chromium muere, el siguiente llenado lo relanza.
        await nav._browser.close()
        async with nav.pagina() as p:
            await p.set_content(FORMULARIO)
            await p.fill("[name='correo']", "ana@ejemplo.com")
            assert await p.input_value("[name='correo']") == "ana@ejemplo.com"
        print("OK — si Chromium se cae, se relanza solo en el siguiente llenado")
    finally:
        await nav.cerrar()


if __name__ == "__main__":
    asyncio.run(main())
