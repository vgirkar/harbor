---
title: Harbor support assistant
emoji: ⚓
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 8000
pinned: false
---

# Harbor on Hugging Face Spaces (free Docker Space)

A free Docker Space gives Harbor a persistent process, 2 vCPUs and 16 GB RAM, which is enough for the local
embedding and reranking models. Steps:

1. Create a Space, SDK = Docker, visibility public, hardware = CPU basic (free).
2. Push this repository to the Space (`git remote add space https://huggingface.co/spaces/<user>/harbor && git push space main`).
   Copy this README to the repository root first: the metadata header above tells the Space which port to expose.
3. In the Space settings add the secrets listed in `deploy/HOSTING.md` (DATABASE_URL, API_KEYS, ADMIN_API_KEY,
   OPENAI_COMPAT_API_KEY) and the variables (LLM_PROVIDER=openai_compatible, EMBEDDINGS_PROVIDER=local,
   EMBEDDING_DIM=384, RERANKER=local, CORS_ORIGINS=https://harbor-support.netlify.app).
4. The container runs `alembic upgrade head` on start. Then call `POST /v1/ingest/knowledge` with the admin key once,
   and `harbor seed` equivalents: `python -c "from harbor.cli import seed; seed()"` from the Space terminal, or load
   `seeds/orders.json` through your own script.
