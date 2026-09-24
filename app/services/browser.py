"""
Navegador Playwright compartido entre llamadas.

Motivo: lanzar Chromium cuesta 1-3 s. Dentro de una llamada telefónica eso es
silencio que el paciente oye mientras Sofía dice que está registrando la cita.
Se lanza una sola vez al arrancar la aplicación y se reutiliza.

Lo que NO se comparte es el contexto. Cada llenado abre su propio
BrowserContext: cookies, almacenamiento y sesión del portal quedan aislados
entre pacientes. Reutilizar un contexto ahorraría unos 50 ms y a cambio
dejaría que la sesión abierta de un paciente en el portal de la clínica
quedara disponible para el siguiente — no es un intercambio aceptable con
datos de salud.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from playwright.async_api import Browser, Page, Playwright, async_playwright

logger = logging.getLogger(__name__)


class NavegadorCompartido:
    def __init__(self, headless: bool = True) -> None:
        self._headless = headless
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        # Serializa el arranque: sin el lock, dos llamadas simultaneas en frio
        # lanzarian dos Chromium y uno quedaria huerfano consumiendo memoria.
        self._lock = asyncio.Lock()

    async def iniciar(self) -> None:
        """Arranca Chromium. Se llama en el lifespan, no en la peticion."""
        async with self._lock:
            await self._asegurar()

    async def _asegurar(self) -> Browser:
        # is_connected(): un servidor que vive dias no puede dar por hecho que
        # Chromium sigue vivo. Si se cayo, se relanza en vez de fallar la cita.
        if self._browser is not None and self._browser.is_connected():
            return self._browser

        if self._browser is not None:
            logger.warning("navegador_caido relanzando")
            self._browser = None

        if self._pw is None:
            self._pw = await async_playwright().start()

        self._browser = await self._pw.chromium.launch(headless=self._headless)
        logger.info("navegador_listo headless=%s", self._headless)
        return self._browser

    @asynccontextmanager
    async def pagina(self) -> AsyncIterator[Page]:
        """Página en contexto propio. Se cierra sola al salir del bloque."""
        async with self._lock:
            browser = await self._asegurar()

        contexto = await browser.new_context()
        try:
            yield await contexto.new_page()
        finally:
            # Cerrar el contexto cierra sus paginas. Si no se cierra, cada
            # llamada deja un contexto vivo y la memoria crece toda la jornada.
            await contexto.close()

    async def cerrar(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None
        logger.info("navegador_cerrado")


# Instancia única del proceso. Con varios workers de uvicorn hay un Chromium
# por worker, que es lo correcto: no se comparte entre procesos.
navegador = NavegadorCompartido()


async def demo() -> None:
    nav = NavegadorCompartido()
    await nav.iniciar()

    async with nav.pagina() as p1:
        await p1.set_content("<input id='x'>")
        await p1.fill("#x", "hola")
        assert await p1.input_value("#x") == "hola"

    # Segunda pagina: mismo navegador, contexto nuevo.
    async with nav.pagina() as p2:
        await p2.set_content("<input id='x'>")
        assert await p2.input_value("#x") == "", "el contexto no quedo aislado"

    # Aislamiento real de cookies entre contextos.
    async with nav.pagina() as p3:
        await p3.goto("about:blank")
        await p3.context.add_cookies([{
            "name": "sesion", "value": "paciente-1",
            "url": "https://ejemplo.com",
        }])
        assert len(await p3.context.cookies("https://ejemplo.com")) == 1

    async with nav.pagina() as p4:
        galletas = await p4.context.cookies("https://ejemplo.com")
        assert galletas == [], f"cookie filtrada entre pacientes: {galletas}"

    await nav.cerrar()
    print("OK — navegador compartido: se reutiliza y aisla cookies entre contextos")


if __name__ == "__main__":
    asyncio.run(demo())
