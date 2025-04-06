FROM python:3.12-slim-bookworm AS builder
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-cache --no-install-project

FROM python:3.12-slim-bookworm
WORKDIR /app

COPY --from=builder /app /app
COPY src /app/src

CMD [ "/app/.venv/bin/python", "-m", "src.main" ]
