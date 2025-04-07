FROM nvidia/cuda:12.8.1-devel-ubuntu22.04 AS builder
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock /app/
RUN  uv venv && uv pip install setuptools torch
ENV CUDA_HOME=/user/local/cuda
RUN  uv sync --no-build-isolation

FROM python:3.12-slim-bookworm
WORKDIR /app

COPY --from=builder /app /app
COPY src /app/src

CMD [ "/app/.venv/bin/python", "-m", "src.main" ]
