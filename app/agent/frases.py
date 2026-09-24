"""
Troceado de la respuesta del LLM en frases pronunciables.

Existe por latencia: si se espera a que el modelo termine la respuesta
completa antes de sintetizar, el paciente oye 2-4 s de silencio cada turno.
Emitiendo por frases, el TTS arranca con la primera mientras el modelo sigue
generando el resto.
"""
from __future__ import annotations

# Cierres de frase. Incluye los signos de apertura invertidos no, esos abren.
_TERMINADORES = ".!?…\n"

# Una frase mas corta que esto no se emite sola: trocear demasiado fino hace
# que el TTS meta una pausa artificial entre fragmentos y suene entrecortado.
# Excepcion: si cierra con ? o !, es una pregunta corta real ("¿Cual?") y se
# emite igual, porque esperar ahi es justo lo que rompe el turno de habla.
_MINIMO = 12

# Abreviaturas frecuentes en una clinica: su punto no cierra frase.
_ABREVIATURAS = ("dr.", "dra.", "sr.", "sra.", "srta.", "ud.", "uds.", "no.", "av.")


def _es_abreviatura(texto: str) -> bool:
    cola = texto.rstrip().lower()
    return any(cola.endswith(a) for a in _ABREVIATURAS)


def extraer_frases(buffer: str, forzar: bool = False) -> tuple[list[str], str]:
    """Saca las frases completas de `buffer`.

    Devuelve (frases, resto). Con `forzar` emite tambien lo que quede sin
    terminador, para el final de la respuesta.
    """
    frases: list[str] = []
    inicio = 0

    for i, ch in enumerate(buffer):
        if ch not in _TERMINADORES:
            continue

        candidata = buffer[inicio : i + 1]
        if _es_abreviatura(candidata):
            continue

        limpia = candidata.strip()
        if not limpia:
            inicio = i + 1
            continue

        # Un decimal ("10.30") o una numeracion no cierran frase.
        if ch == "." and i + 1 < len(buffer) and buffer[i + 1].isdigit():
            continue

        if len(limpia) < _MINIMO and ch not in "?!":
            continue

        frases.append(limpia)
        inicio = i + 1

    resto = buffer[inicio:]
    if forzar and resto.strip():
        frases.append(resto.strip())
        resto = ""

    return frases, resto


def demo() -> None:
    f, r = extraer_frases("Hola, soy Sofía. ¿En qué te ayudo?")
    assert f == ["Hola, soy Sofía.", "¿En qué te ayudo?"], f
    assert r == "", repr(r)

    # Frase incompleta: se queda en el resto esperando mas tokens.
    f, r = extraer_frases("Tu cita quedó para el martes")
    assert f == [] and r == "Tu cita quedó para el martes", (f, r)

    # Abreviatura: el punto de "Dr." no parte la frase.
    f, r = extraer_frases("Te atiende el Dr. Ramírez en consultorio dos.")
    assert f == ["Te atiende el Dr. Ramírez en consultorio dos."], f

    # Decimal: no parte.
    f, r = extraer_frases("El costo es 120.500 pesos colombianos.")
    assert f == ["El costo es 120.500 pesos colombianos."], f

    # Pregunta corta: se emite aunque sea breve, porque esperar ahi rompe el turno.
    f, r = extraer_frases("¿Cuál?")
    assert f == ["¿Cuál?"], f

    # Fragmento corto sin ? ni !: espera a juntarse con lo siguiente.
    f, r = extraer_frases("Ya. Listo entonces, nos vemos el jueves.")
    assert f == ["Ya. Listo entonces, nos vemos el jueves."], f

    # forzar: cierra lo que quede al terminar la respuesta.
    f, r = extraer_frases("sin punto final", forzar=True)
    assert f == ["sin punto final"] and r == "", (f, r)

    print("OK — troceado en frases: abreviaturas, decimales, preguntas cortas y cierre forzado")


if __name__ == "__main__":
    demo()
