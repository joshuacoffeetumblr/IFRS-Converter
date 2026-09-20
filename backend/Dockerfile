# syntax=docker/dockerfile:1

# Two stages, for one reason: the build needs a compiler and the runtime must
# not have one. The dev extra (pytest, ruff, mypy, reportlab) is never
# installed here either — a production image should not carry the tools that
# test it (spec §31).

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir ".[ai]"


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /srv

# curl is the healthcheck's only dependency; build-essential does not follow.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
COPY docker-entrypoint.sh ./

# Do not run as root (spec §31). The upload directory is the one path the
# process writes to, and it is created here so the container does not need a
# writable /srv.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /srv/var/uploads \
    && chmod +x /srv/docker-entrypoint.sh \
    && chown -R appuser:appuser /srv
USER appuser

EXPOSE 8000
# `$PORT` where a platform assigns one (Render, Fly), 8000 otherwise.
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
    CMD curl -fsS "http://localhost:${PORT:-8000}/api/health" || exit 1

# Migrate, seed, then serve. The logic is in the script so no platform's
# quoting rules can come between the intent and what runs.
CMD ["/srv/docker-entrypoint.sh"]
