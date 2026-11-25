"""Typed runtime configuration.

Every knob is an environment variable (see .env.example). Settings are validated once at import time so
misconfiguration fails at boot, not on the first request.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Runtime
    app_env: Literal["dev", "prod", "test"] = "dev"
    log_level: str = "INFO"
    api_keys: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["dev-key"])
    admin_api_key: str = "dev-admin-key"
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:8000"])
    rate_limit_per_minute: int = 60

    # Postgres
    database_url: str = "postgresql+psycopg://harbor:harbor@localhost:5432/harbor"

    # LLM
    llm_provider: Literal["anthropic", "openai_compatible", "fake"] = "anthropic"
    # OpenAI-compatible endpoint: Groq, Gemini (OpenAI endpoint), OpenRouter, Cerebras, Ollama, vLLM ...
    openai_compat_base_url: str = "http://localhost:11434/v1"
    openai_compat_api_key: str = "ollama"
    openai_compat_answer_model: str = "qwen2.5:3b"
    openai_compat_fast_model: str = "qwen2.5:3b"
    openai_compat_verifier_model: str = "qwen2.5:3b"
    openai_compat_structured_method: Literal["function_calling", "json_schema", "json_mode"] = (
        "function_calling"
    )
    anthropic_api_key: str | None = None
    answer_model: str = "claude-opus-5-5"
    fast_model: str = "claude-haiku-4-5"
    verifier_model: str = "claude-sonnet-5-5"
    answer_effort: Literal["low", "medium", "high"] = "medium"
    answer_max_tokens: int = 1500

    # Embeddings
    embeddings_provider: Literal["voyage", "openai", "local", "fake"] = "voyage"
    local_embedding_model: str = "BAAI/bge-small-en-v1.5"  # 384 dims, runs on CPU through fastembed (ONNX)
    voyage_api_key: str | None = None
    embedding_model: str = "voyage-3.5"
    embedding_dim: int = 1024
    openai_api_key: str | None = None
    openai_embedding_model: str = "text-embedding-3-small"

    # Vector index
    vector_backend: Literal["pgvector", "pinecone"] = "pgvector"
    pinecone_api_key: str | None = None
    pinecone_index: str = "harbor-chunks"
    pinecone_namespace: str = "default"

    # Retrieval
    reranker: Literal["voyage", "local", "llm", "none"] = "voyage"
    local_rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"  # cross-encoder through fastembed
    rerank_model: str = "rerank-2.5"
    dense_top_k: int = 20
    lexical_top_k: int = 20
    rerank_top_n: int = 6
    rrf_k: int = 60
    min_relevance: float = 0.15

    # Guardrails
    guard_llm_mode: Literal["always", "uncertain", "never"] = "uncertain"
    guard_block_threshold: float = 0.7  # LLM classifier confidence needed to block
    guard_hard_block_score: float = 0.9  # heuristic score that blocks without asking the LLM
    max_answer_attempts: int = 2

    # Voice
    speech_provider: Literal["elevenlabs", "openai", "fake"] = "elevenlabs"
    elevenlabs_api_key: str | None = None
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"
    elevenlabs_tts_model: str = "eleven_flash_v2_5"
    elevenlabs_stt_model: str = "scribe_v1"
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_stt_model: str = "gpt-4o-mini-transcribe"

    @field_validator("api_keys", "cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def psycopg_dsn(self) -> str:
        """Plain libpq DSN for the LangGraph checkpointer (it does not speak SQLAlchemy URLs)."""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    @property
    def is_offline(self) -> bool:
        """True when every external provider is the deterministic fake (tests, demos without keys)."""
        return (
            self.llm_provider == "fake"
            and self.embeddings_provider == "fake"
            and self.speech_provider == "fake"
            and self.reranker != "voyage"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
