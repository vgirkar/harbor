"""Data access. Thin, explicit SQL where it matters (hybrid search), ORM for the rest."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from harbor.db.models import Chunk, Conversation, Customer, Document, Message, Order, Ticket

_WORD = re.compile(r"[a-z0-9]+")


@dataclass(slots=True)
class ScoredId:
    chunk_id: uuid.UUID
    score: float


# --------------------------------------------------------------------------------------------------
# Documents and chunks
# --------------------------------------------------------------------------------------------------
class DocumentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_source(self, source: str) -> Document | None:
        return await self.session.scalar(select(Document).where(Document.source == source))

    async def upsert_document(
        self, *, source: str, title: str, content_hash: str, metadata: dict[str, Any]
    ) -> Document:
        doc = await self.get_by_source(source)
        if doc is None:
            doc = Document(source=source, title=title, content_hash=content_hash, metadata_=metadata)
            self.session.add(doc)
            await self.session.flush()
        else:
            doc.title, doc.content_hash, doc.metadata_ = title, content_hash, metadata
        return doc

    async def delete_chunks(self, document_id: uuid.UUID) -> list[uuid.UUID]:
        ids = list(await self.session.scalars(select(Chunk.id).where(Chunk.document_id == document_id)))
        await self.session.execute(delete(Chunk).where(Chunk.document_id == document_id))
        return ids

    async def add_chunks(self, chunks: list[Chunk]) -> None:
        self.session.add_all(chunks)
        await self.session.flush()

    async def get_chunks(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, Chunk]:
        if not ids:
            return {}
        rows = await self.session.scalars(select(Chunk).where(Chunk.id.in_(ids)).options())
        result: dict[uuid.UUID, Chunk] = {}
        for chunk in rows:
            await self.session.refresh(chunk, attribute_names=["document"])
            result[chunk.id] = chunk
        return result

    async def count_chunks(self) -> int:
        return int(await self.session.scalar(text("SELECT count(*) FROM chunks")) or 0)

    async def list_documents(self) -> list[Document]:
        return list(await self.session.scalars(select(Document).order_by(Document.source)))

    async def all_chunks_for_reindex(self) -> list[Chunk]:
        return list(await self.session.scalars(select(Chunk).where(Chunk.quarantined.is_(False))))

    # ---- retrieval primitives -------------------------------------------------------------------
    async def dense_search(self, query_vector: list[float], k: int) -> list[ScoredId]:
        """Cosine similarity through the HNSW index. `<=>` is cosine distance, so similarity = 1 - distance."""
        vector_literal = "[" + ",".join(f"{v:.7f}" for v in query_vector) + "]"
        rows = await self.session.execute(
            text(
                """
                SELECT id, 1 - (embedding <=> CAST(:q AS vector)) AS score
                FROM chunks
                WHERE embedding IS NOT NULL AND quarantined = false
                ORDER BY embedding <=> CAST(:q AS vector)
                LIMIT :k
                """
            ),
            {"q": vector_literal, "k": k},
        )
        return [ScoredId(chunk_id=row.id, score=float(row.score)) for row in rows]

    async def lexical_search(self, query: str, k: int) -> list[ScoredId]:
        """Full-text search over the generated tsvector.

        Terms are OR-ed (websearch syntax) so a natural-language question still matches chunks that contain
        some of its words; ts_rank_cd with normalisation 32 scales scores into 0..1 and rewards density.
        Stop words and stemming come from the 'english' configuration, the same one used by the generated column.
        """
        terms = [t for t in _WORD.findall(query.lower()) if len(t) > 1]
        if not terms:
            return []
        rows = await self.session.execute(
            text(
                """
                SELECT id, ts_rank_cd(tsv, q, 32) AS score
                FROM chunks, websearch_to_tsquery('english', :query) AS q
                WHERE tsv @@ q AND quarantined = false
                ORDER BY score DESC
                LIMIT :k
                """
            ),
            {"query": " or ".join(dict.fromkeys(terms)), "k": k},
        )
        return [ScoredId(chunk_id=row.id, score=float(row.score)) for row in rows]


# --------------------------------------------------------------------------------------------------
# Conversations and messages
# --------------------------------------------------------------------------------------------------
class ConversationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(
        self, conversation_id: uuid.UUID | None, *, channel: str, customer_email: str | None
    ) -> Conversation:
        if conversation_id is not None:
            existing = await self.session.get(Conversation, conversation_id)
            if existing is not None:
                if customer_email and not existing.customer_email:
                    existing.customer_email = customer_email
                return existing
        conv = Conversation(
            id=conversation_id or uuid.uuid4(), channel=channel, customer_email=customer_email
        )
        self.session.add(conv)
        await self.session.flush()
        return conv

    async def add_message(self, conversation_id: uuid.UUID, **fields: Any) -> Message:
        msg = Message(conversation_id=conversation_id, **fields)
        self.session.add(msg)
        await self.session.flush()
        return msg

    async def transcript(self, conversation_id: uuid.UUID) -> list[Message]:
        rows = await self.session.scalars(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at)
        )
        return list(rows)

    async def get(self, conversation_id: uuid.UUID) -> Conversation | None:
        return await self.session.get(Conversation, conversation_id)


# --------------------------------------------------------------------------------------------------
# Orders (the "business system" the order agent talks to through tools)
# --------------------------------------------------------------------------------------------------
class OrderRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_order(self, order_number: str, email: str) -> dict[str, Any] | None:
        """Authorization is enforced here, not in the prompt: the order must belong to this email."""
        stmt = (
            select(Order, Customer)
            .join(Customer, Customer.id == Order.customer_id)
            .where(
                Order.order_number == order_number.upper().strip(), Customer.email == email.lower().strip()
            )
        )
        row = (await self.session.execute(stmt)).first()
        if row is None:
            return None
        order, customer = row
        return _order_to_dict(order, customer)

    async def list_orders(self, email: str, limit: int = 5) -> list[dict[str, Any]]:
        stmt = (
            select(Order, Customer)
            .join(Customer, Customer.id == Order.customer_id)
            .where(Customer.email == email.lower().strip())
            .order_by(Order.placed_at.desc())
            .limit(limit)
        )
        return [
            _order_to_dict(order, customer) for order, customer in (await self.session.execute(stmt)).all()
        ]

    async def seed(self, customers: list[dict[str, Any]], orders: list[dict[str, Any]]) -> int:
        by_email: dict[str, Customer] = {}
        for c in customers:
            existing = await self.session.scalar(select(Customer).where(Customer.email == c["email"]))
            if existing is None:
                existing = Customer(email=c["email"], name=c["name"])
                self.session.add(existing)
                await self.session.flush()
            by_email[c["email"]] = existing
        created = 0
        for o in orders:
            if await self.session.scalar(select(Order).where(Order.order_number == o["order_number"])):
                continue
            self.session.add(Order(customer_id=by_email[o.pop("customer_email")].id, **o))
            created += 1
        await self.session.flush()
        return created


def _order_to_dict(order: Order, customer: Customer) -> dict[str, Any]:
    def iso(value: datetime | None) -> str | None:
        return value.isoformat() if value else None

    return {
        "order_number": order.order_number,
        "customer_name": customer.name,
        "status": order.status,
        "total": float(order.total),
        "currency": order.currency,
        "items": order.items,
        "carrier": order.carrier,
        "tracking_number": order.tracking_number,
        "placed_at": iso(order.placed_at),
        "shipped_at": iso(order.shipped_at),
        "delivered_at": iso(order.delivered_at),
    }


# --------------------------------------------------------------------------------------------------
# Tickets
# --------------------------------------------------------------------------------------------------
class TicketRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self, *, conversation_id: uuid.UUID | None, customer_email: str | None, summary: str, priority: str
    ) -> Ticket:
        ticket = Ticket(
            conversation_id=conversation_id, customer_email=customer_email, summary=summary, priority=priority
        )
        self.session.add(ticket)
        await self.session.flush()
        return ticket
