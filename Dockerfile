# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

FROM base AS deps
COPY pyproject.toml README.md ./
COPY harbor ./harbor
RUN pip install --upgrade pip && pip install ".[evals,free]"

FROM base AS runtime
ARG PRELOAD_LOCAL_MODELS=0
ENV FASTEMBED_CACHE_PATH=/app/.cache/fastembed
RUN useradd --create-home --uid 10001 harbor && mkdir -p /app/.cache/fastembed && chown -R harbor:harbor /app
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin
COPY harbor ./harbor
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY knowledge ./knowledge
COPY seeds ./seeds
COPY static ./static
RUN chown -R harbor:harbor /app
USER harbor
RUN if [ "$PRELOAD_LOCAL_MODELS" = "1" ]; then python -c "from fastembed import TextEmbedding; from fastembed.rerank.cross_encoder import TextCrossEncoder; TextEmbedding('BAAI/bge-small-en-v1.5'); TextCrossEncoder('Xenova/ms-marco-MiniLM-L-6-v2')"; fi
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["sh", "-c", "alembic upgrade head && uvicorn harbor.api.app:app --host 0.0.0.0 --port 8000 --workers 1"]
