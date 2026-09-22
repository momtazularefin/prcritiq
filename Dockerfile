# PRCritiq service image (ADR-019).
#
# Two stages: uv resolves the locked environment in the first, and the second
# carries only that environment and the source, running as an unprivileged user.
#
#   docker build -t prcritiq .
#   docker run --rm -p 8000:8000 prcritiq

FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.11.23 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, so a source-only change reuses this layer.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --group tools --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev --group tools


FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN useradd --system --uid 10001 --no-create-home --home-dir /nonexistent prcritiq

WORKDIR /app
COPY --from=build --chown=root:root /app /app

USER prcritiq
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]

# Proxy headers are trusted from any peer because the only peer that can reach
# this port is the reverse proxy on the private Compose network; the port is
# never published on the host. The rate limit keys on the forwarded address.
CMD ["uvicorn", "prcritiq.api:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
