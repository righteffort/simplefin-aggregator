# syntax=docker/dockerfile:1
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install dependencies first, separately from app code, so dependency layers
# stay cached across source-only changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src ./src
COPY README.md ./
RUN uv sync --frozen --no-dev

# Loopback only. Do not publish this port to anything but 127.0.0.1 on the host.
EXPOSE 8080

ENTRYPOINT ["uv", "run", "simplefin-aggregator"]
CMD ["serve", "--config", "/config/config.toml"]
