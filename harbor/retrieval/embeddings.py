"""Embedding providers behind one small protocol.

voyage  : Voyage AI (Anthropic's recommended embeddings partner), asymmetric query/document inputs
openai  : text-embedding-3-small, optional extra
local   : fastembed (ONNX on CPU, no key): BAAI/bge-small-en-v1.5 by default, 384 dims, free
fake    : deterministic hashed bag-of-words vectors so the whole pipeline runs offline in tests and demos
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from typing import Protocol

from harbor.config import Settings


class EmbeddingsProvider(Protocol):
    dim: int
    model_name: str

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class VoyageEmbeddings:
    def __init__(self, api_key: str, model: str, dim: int, batch_size: int = 64):
        from langchain_voyageai import VoyageAIEmbeddings

        self.model_name = model
        self.dim = dim
        self._client = VoyageAIEmbeddings(voyage_api_key=api_key, model=model, batch_size=batch_size)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._client.aembed_documents(texts)

    async def embed_query(self, text: str) -> list[float]:
        return await self._client.aembed_query(text)


class OpenAIEmbeddings:
    def __init__(self, api_key: str, model: str, dim: int):
        from langchain_openai import OpenAIEmbeddings as _OpenAIEmbeddings

        self.model_name = model
        self.dim = dim
        self._client = _OpenAIEmbeddings(api_key=api_key, model=model, dimensions=dim)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._client.aembed_documents(texts)

    async def embed_query(self, text: str) -> list[float]:
        return await self._client.aembed_query(text)


class LocalEmbeddings:
    """fastembed runs quantised ONNX models on CPU; the first call downloads the model (~130 MB)."""

    def __init__(self, model: str, dim: int):
        from fastembed import TextEmbedding

        self.model_name = model
        self.dim = dim
        self._model = TextEmbedding(model)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(lambda: [v.tolist() for v in self._model.embed(texts, batch_size=32)])

    async def embed_query(self, text: str) -> list[float]:
        return await asyncio.to_thread(lambda: next(iter(self._model.query_embed(text))).tolist())


class FakeEmbeddings:
    """Hashed bag-of-words into `dim` buckets, L2 normalised. Lexically similar texts land close together,
    which is enough to exercise dense search, fusion and reranking deterministically."""

    _token = re.compile(r"[a-z0-9]+")

    @staticmethod
    def stem(tok: str) -> str:
        """Tiny suffix stripper so offline retrieval sees return/returns/returning as one term."""
        for suffix in ("ing", "ies", "ed", "es", "s"):
            if len(tok) > len(suffix) + 3 and tok.endswith(suffix):
                return tok[: -len(suffix)] + ("y" if suffix == "ies" else "")
        return tok

    def __init__(self, dim: int = 1024):
        self.dim = dim
        self.model_name = "fake-hashed-bow"

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for raw in self._token.findall(text.lower()):
            if len(raw) < 3:
                continue
            tok = self.stem(raw)
            h = int(hashlib.blake2b(tok.encode(), digest_size=8).hexdigest(), 16)
            vec[h % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


def build_embeddings(settings: Settings) -> EmbeddingsProvider:
    if settings.embeddings_provider == "voyage":
        if not settings.voyage_api_key:
            raise RuntimeError("EMBEDDINGS_PROVIDER=voyage requires VOYAGE_API_KEY")
        return VoyageEmbeddings(settings.voyage_api_key, settings.embedding_model, settings.embedding_dim)
    if settings.embeddings_provider == "openai":
        if not settings.openai_api_key:
            raise RuntimeError("EMBEDDINGS_PROVIDER=openai requires OPENAI_API_KEY")
        return OpenAIEmbeddings(
            settings.openai_api_key, settings.openai_embedding_model, settings.embedding_dim
        )
    if settings.embeddings_provider == "local":
        return LocalEmbeddings(settings.local_embedding_model, settings.embedding_dim)
    return FakeEmbeddings(settings.embedding_dim)
