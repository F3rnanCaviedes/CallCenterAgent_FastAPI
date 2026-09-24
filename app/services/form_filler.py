"""Rellena formularios web con Playwright.

Abre una URL en un navegador controlado y escribe los valores en los inputs
localizándolos por atributo (name/id/placeholder), sin depender de coordenadas
ni del orden de tabulación.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

from playwright.async_api import Page, async_playwright

from app.services.browser import navegador

from app.config import get_settings

logger = logging.getLogger(__name__)

FIELD_ORDER: tuple[str, ...] = (
    "fecha",
    "nombre",
    "apellido",
    "telefono",
    "direccion",
    "correo",
    "descripcion",
)
OPTIONAL_FIELDS: frozenset[str] = frozenset({"descripcion"})

# Alias por campo. El primero que exista en la página gana.
# ponytail: coincidencia por subcadena de atributo; si un sitio usa nombres
# raros, pásalo en `selectors` en vez de ampliar esta tabla.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "fecha": ("fecha", "date"),
    "nombre": ("nombre", "first_name", "firstname", "given"),
    "apellido": ("apellido", "last_name", "lastname", "surname"),
    "telefono": ("telefono", "phone", "celular", "movil", "tel"),
    "direccion": ("direccion", "address", "addr", "calle"),
    "correo": ("correo", "email", "mail"),
    "descripcion": ("descripcion", "description", "comentario", "mensaje", "notas", "observ"),
}


def _clean(text: str) -> str:
    return "".join(c for c in str(text) if c not in "\t\r\x0b\x0c").strip()


def _plan(values: dict[str, str]) -> list[tuple[str, str]]:
    """Valida y devuelve la secuencia (campo, texto) a escribir.

    Los campos obligatorios vacíos lanzan ValueError; los opcionales vacíos
    se omiten.
    """
    missing = [f for f in FIELD_ORDER if f not in OPTIONAL_FIELDS and not _clean(values.get(f, ""))]
    if missing:
        raise ValueError(f"campos obligatorios vacíos: {', '.join(missing)}")
    return [(f, _clean(values.get(f, ""))) for f in FIELD_ORDER if _clean(values.get(f, ""))]


def _selector(field: str) -> str:
    """CSS que apunta a cualquier input/textarea cuyo name/id/placeholder coincida."""
    return ", ".join(
        f'input[{attr}*="{alias}" i], textarea[{attr}*="{alias}" i]'
        for alias in FIELD_ALIASES[field]
        for attr in ("name", "id", "placeholder")
    )


def _check_url(url: str, allowed_hosts: list[str]) -> str:
    """La URL viene del LLM/usuario: solo http(s) y hosts autorizados."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("URL inválida: solo se admite http/https")
    host = parsed.hostname.lower()
    if not any(host == h.lower() or host.endswith("." + h.lower()) for h in allowed_hosts):
        raise ValueError(f"host no autorizado: {host}")
    return url


async def _fill_fields(
    page: Page,
    plan: list[tuple[str, str]],
    selectors: dict[str, str] | None = None,
    timeout: float = 5000,
) -> dict[str, str]:
    """Escribe cada campo del plan. Devuelve solo los que se encontraron."""
    filled: dict[str, str] = {}
    for field, text in plan:
        css = (selectors or {}).get(field) or _selector(field)
        target = page.locator(css).first
        try:
            await target.fill(text, timeout=timeout)
            filled[field] = text
        except Exception:
            if field not in OPTIONAL_FIELDS:
                raise ValueError(f"campo no encontrado en la página: {field}")
            logger.warning("campo opcional omitido: %s", field)
    return filled


async def fill_form(
    url: str,
    values: dict[str, str],
    *,
    selectors: dict[str, str] | None = None,
    timeout: float = 5000,
    submit: bool = False,
) -> dict[str, str]:
    """Abre `url` y registra `values` en su formulario. Devuelve lo escrito.

    Calibración por sitio (el DOM real nunca coincide con el ideal):
      selectors   CSS explícito por campo cuando los alias no aciertan
      timeout     ms de espera por campo en formularios que cargan tarde
    """
    plan = _plan(values)  # valida antes de abrir el navegador
    _check_url(url, get_settings().form_filler_allowed_hosts)

    # Navegador compartido: lanzarlo aqui costaria 1-3 s de silencio en mitad
    # de la llamada. Cada llenado usa su propio contexto, asi que la sesion del
    # portal no se filtra entre pacientes.
    async with navegador.pagina() as page:
        await page.goto(url, wait_until="domcontentloaded")
        filled = await _fill_fields(page, plan, selectors, timeout)
        if submit:
            await page.locator(
                'button[type="submit"], input[type="submit"]'
            ).first.click(timeout=timeout)
            await page.wait_for_load_state("networkidle")

    logger.info("form_filled url=%s campos=%d submit=%s", url, len(filled), submit)
    return filled


async def demo() -> None:
    assert _clean("Ana\tMaría") == "AnaMaría"
    assert _clean("  Bogotá  ") == "Bogotá"

    hosts = ["clinica.com"]
    assert _check_url("https://citas.clinica.com/f", hosts)
    for bad in ("file:///etc/passwd", "https://evil.com", "https://noclinica.com"):
        try:
            _check_url(bad, hosts)
        except ValueError:
            pass
        else:
            raise AssertionError(f"URL no bloqueada: {bad}")

    full = {
        "fecha": "2026-08-20",
        "nombre": "Ana",
        "apellido": "Muñoz",
        "telefono": "3001234567",
        "direccion": "Cra 7 #1-2",
        "correo": "ana@x.com",
        "descripcion": "Control",
    }
    assert [f for f, _ in _plan(full)] == list(FIELD_ORDER)
    sin_desc = {k: v for k, v in full.items() if k != "descripcion"}
    assert [f for f, _ in _plan(sin_desc)] == list(FIELD_ORDER[:-1])
    try:
        _plan({k: v for k, v in full.items() if k != "correo"})
    except ValueError as exc:
        assert "correo" in str(exc)
    else:
        raise AssertionError("faltó validar campo obligatorio")

    # Formulario real con nombres de atributo variados, contra un navegador real.
    html = """
    <form>
      <input name="fecha_cita"><input id="firstName"><input name="user_lastname">
      <input placeholder="Celular"><input name="direccion_residencia">
      <input name="correo_electronico" type="email"><textarea id="comentarios"></textarea>
    </form>
    """
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        filled = await _fill_fields(page, _plan(full))
        assert set(filled) == set(FIELD_ORDER), filled
        assert await page.locator("#firstName").input_value() == "Ana"
        assert await page.locator("#comentarios").input_value() == "Control"
        assert await page.locator('[name="user_lastname"]').input_value() == "Muñoz"
        await browser.close()

    print("ok")


if __name__ == "__main__":
    import asyncio

    asyncio.run(demo())
