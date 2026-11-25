# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

FROM base AS deps
COPY pyproject.toml README.md ./
COPY harbor ./harbor
RUN pip install --upgrade pip && pip install ".[evals]"

FROM base AS runtime
RUN useradd --create-home --uid 10001 harbor
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin
COPY harbor ./harbor
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY knowledge ./knowledge
COPY seeds ./seeds
COPY static ./static
USER harbor
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["sh", "-c", "alembic upgrade head && uvicorn harbor.api.app:app --host 0.0.0.0 --port 8000 --workers 1"]
