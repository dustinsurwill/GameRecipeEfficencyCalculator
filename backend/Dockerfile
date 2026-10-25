FROM python:3.13-slim

ENV UV_COMPILE_BYTECODE=1
ENV UV_NO_DEV=1
ENV UV_LINK_MODE=copy
# glibc keeps one malloc arena per thread; two is plenty and avoids RSS bloat from fragmentation
ENV MALLOC_ARENA_MAX=2
ENV PYTHONUNBUFFERED=1
# Run the synced virtualenv directly; `uv run` needs a writable cache the non-root user lacks
ENV PATH="/app/.venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

# Copy application source
COPY . .

RUN useradd --system --no-create-home app && chown -R app /app
USER app

EXPOSE 8000

# One worker on purpose: the discovery index and recipe graph are cached per process, so each
# extra worker duplicates ~250-450 MB. Scale with replicas behind a proxy if you need to.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
