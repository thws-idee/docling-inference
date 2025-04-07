FROM nvidia/cuda:12.8.1-devel-ubuntu22.04 AS builder
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock /app/
RUN  uv venv && uv pip install setuptools torch
RUN  uv sync --no-build-isolation --frozen --no-cache --no-install-project

FROM python:3.13-slim-bookworm
WORKDIR /app

COPY --from=builder /app /app
COPY src /app/src
RUN unlink /app/.venv/bin/python && ln -s /usr/local/bin/python /app/.venv/bin/python
RUN chmod 755 /app/.venv/bin/activate
RUN /app/.venv/bin/activate

CMD [ "/app/.venv/bin/python", "-m", "src.main" ]
