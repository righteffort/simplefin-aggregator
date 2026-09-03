# syntax=docker/dockerfile:1
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /bin/

WORKDIR /app

ENV UV_PYTHON=/usr/local/bin/python3.14
ENV UV_PYTHON_PREFERENCE=only-system

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src ./src
COPY README.md ./
RUN uv sync --frozen --no-dev

ENV UV_CACHE_DIR=/tmp/uv-cache

# Loopback only. Do not publish this port to anything but 127.0.0.1 on the host.
EXPOSE 8080

ENTRYPOINT ["uv", "run", "--no-sync", "simplefin-aggregator"]
CMD ["serve", "--config", "/config/config.toml"]
