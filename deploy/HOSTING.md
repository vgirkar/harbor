# Running Harbor on free tiers

Harbor needs three things: a container host for the API, a Postgres with pgvector, and an LLM endpoint. Embeddings and
reranking run inside the container for free (fastembed). Each part below has a free tier that needs only a sign-up.

| Part | Free option | Notes |
|---|---|---|
| Front end | Netlify (static) | The demo page is deployed at https://harbor-support.netlify.app; enter the API URL in its Backend URL box. `netlify deploy --prod --dir static` redeploys it. |
| API | Hugging Face Spaces (Docker, CPU basic) or Render (free web service) | Spaces has 16 GB RAM and never sleeps while public; Render free has 512 MB and sleeps after 15 min. Prefer Spaces for the local models. |
| Postgres + pgvector | Neon (free project) or Supabase (free project) | Both ship the `vector` extension. Use the pooled connection string with `sslmode=require` and the `postgresql+psycopg://` scheme. |
| LLM | Groq (free tier) | `OPENAI_COMPAT_BASE_URL=https://api.groq.com/openai/v1`, answer and verifier model `llama-3.3-70b-versatile`, fast model `llama-3.1-8b-instant`, `OPENAI_COMPAT_STRUCTURED_METHOD=function_calling`. Gemini's OpenAI endpoint and OpenRouter `:free` models work the same way. |
| Embeddings and reranker | local (fastembed) | `EMBEDDINGS_PROVIDER=local`, `EMBEDDING_DIM=384`, `RERANKER=local`. No key. Models download on first start (about 130 MB) unless baked into the image with `--build-arg PRELOAD_LOCAL_MODELS=1`. |
| Voice | ElevenLabs free tier (10k credits per month) | `SPEECH_PROVIDER=elevenlabs` + `ELEVENLABS_API_KEY`; or leave `fake` to keep the voice endpoint working without audio synthesis. |

## Environment for the API container

```
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST/DB?sslmode=require
API_KEYS=<a long random string>
ADMIN_API_KEY=<another long random string>
CORS_ORIGINS=https://harbor-support.netlify.app
APP_ENV=prod
LLM_PROVIDER=openai_compatible
OPENAI_COMPAT_BASE_URL=https://api.groq.com/openai/v1
OPENAI_COMPAT_API_KEY=<groq key>
OPENAI_COMPAT_ANSWER_MODEL=llama-3.3-70b-versatile
OPENAI_COMPAT_FAST_MODEL=llama-3.1-8b-instant
OPENAI_COMPAT_VERIFIER_MODEL=llama-3.3-70b-versatile
EMBEDDINGS_PROVIDER=local
EMBEDDING_DIM=384
RERANKER=local
SPEECH_PROVIDER=fake
```

## First run

1. The container applies migrations on start (`alembic upgrade head`); the vector column takes its dimension from
   `EMBEDDING_DIM`, so set it before the first start.
2. Ingest the knowledge base: `curl -X POST $API/v1/ingest/knowledge -H "x-api-key: $ADMIN_API_KEY"`.
3. Seed sample orders (one-off): run `harbor seed` with the same `DATABASE_URL` from your laptop, or skip orders.
4. Open https://harbor-support.netlify.app, enter the API URL and a client key, and chat.

## Local, fully free, no accounts at all

```
pip install -e ".[free]"
# Ollama from https://ollama.com (or the GitHub release binary), then: ollama pull qwen2.5:3b
EMBEDDING_DIM=384 EMBEDDINGS_PROVIDER=local RERANKER=local LLM_PROVIDER=openai_compatible make db migrate seed ingest serve
```
