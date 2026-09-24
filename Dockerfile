# syntax=docker/dockerfile:1
FROM python:3.11-slim

# El form-filler necesita Chromium (~700 MB con sus librerías de sistema).
# Si se despliega v1 sin esa función (form_filler_allowed_hosts vacío),
# construir con --build-arg INSTALL_BROWSER=false y ahorrarse el peso.
ARG INSTALL_BROWSER=true

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

WORKDIR /app

# Las dependencias van antes que el código: cambiar una línea de la app
# no debe invalidar la capa que instala ~40 paquetes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# `playwright install --with-deps` no sirve aquí: asume Ubuntu y pide paquetes
# de fuentes (ttf-unifont, ttf-ubuntu-font-family) que Debian no tiene, y aborta
# la instalación entera. Se instalan las librerías de sistema a mano y luego
# Playwright sólo descarga el binario del navegador.
RUN if [ "$INSTALL_BROWSER" = "true" ]; then \
        apt-get update && apt-get install -y --no-install-recommends \
            libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
            libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
            libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 \
            libasound2 libatspi2.0-0 fonts-liberation \
        && rm -rf /var/lib/apt/lists/* \
        && playwright install chromium; \
    else \
        echo "Chromium omitido (INSTALL_BROWSER=false)"; \
    fi

COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY app/ ./app/

# Sin root: si alguien escapa del proceso, no es administrador del contenedor.
# mkdir -p: con INSTALL_BROWSER=false el directorio de Playwright nunca se
# crea y el chown fallaría.
RUN useradd --create-home --uid 10001 sofia \
    && mkdir -p /opt/playwright \
    && chown -R sofia:sofia /app /opt/playwright
USER sofia

EXPOSE 8000

# Contra /health/ready: comprueba Postgres y Redis, no sólo que el proceso viva.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/ready',timeout=4).status==200 else 1)"

# Las migraciones NO corren aquí a propósito: con varias réplicas arrancando
# a la vez competirían por el mismo upgrade. Ejecutar `alembic upgrade head`
# como paso previo y separado del despliegue.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
