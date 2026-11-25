"""Protocols the graph depends on. The API wires Postgres-backed implementations; tests wire in-memory ones."""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from harbor.retrieval.types import RetrievedChunk


class Retriever(Protocol):
    async def retrieve(self, query: str, *, top_n: int | None = None) -> list[RetrievedChunk]: ...


class OrderService(Protocol):
    async def get_order(self, order_number: str, email: str) -> dict[str, Any] | None: ...

    async def list_orders(self, email: str, limit: int = 5) -> list[dict[str, Any]]: ...


class TicketService(Protocol):
    async def create(
        self, *, conversation_id: uuid.UUID | None, customer_email: str | None, summary: str, priority: str
    ) -> str: ...
