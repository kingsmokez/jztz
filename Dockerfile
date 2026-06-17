# syntax=docker/dockerfile:1.6
# Multi-stage build for jztz_v17.
#   builder  : installs requirements into /install
#   runtime  : minimal image with just the installed packages + app code
# Image target size: ~250 MB (slim + 11 wheels).
FROM python:3.11-slim AS builder

WORKDIR /build

# Build deps (gcc for any C extensions, libffi for cryptography transitively).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# --prefix=/install so we can copy the whole virtual-env tree in one shot
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# Non-root user (uid/gid 1000) for runtime.
RUN groupadd --system --gid 1000 jztz \
    && useradd  --system --uid 1000 --gid jztz --create-home --shell /bin/bash jztz

# curl is needed by HEALTHCHECK; keep the rest of the image lean.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy installed packages from the builder stage.
COPY --from=builder /install /usr/local

# Copy the application source.
COPY --chown=jztz:jztz . /app

# Logs directory is writable by jztz.
RUN mkdir -p /app/logs && chown -R jztz:jztz /app/logs

USER jztz

ENV PORT=5000 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GUNICORN_WORKERS=2 \
    GUNICORN_THREADS=4

EXPOSE 5000

# Liveness probe — /api/live is always 200 if the process is up
# (see modules.health in web_app.py:117).  30s cadence keeps the load low
# while still detecting a wedged worker within ~90s.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail --silent http://localhost:${PORT}/api/live || exit 1

# gunicorn is the production WSGI server.  gthread worker-class is
# friendly to the SSE endpoint which holds long-lived connections.
CMD ["sh", "-c", "exec gunicorn \
    --workers ${GUNICORN_WORKERS} \
    --threads ${GUNICORN_THREADS} \
    --bind 0.0.0.0:${PORT} \
    --worker-class gthread \
    --timeout 300 --graceful-timeout 300 \
    --access-logfile - \
    --error-logfile - \
    web_app:app"]
