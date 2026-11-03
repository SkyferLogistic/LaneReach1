"""Keeping the help search index in step with the shipped articles and the node registry.

Only sections whose text changed are embedded again, and embeddings are reused across tenants
within one run (the help text is the same for everyone), so a deploy costs one embedding call per
changed section, not one per tenant.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from lanereach.assistant.embeddings import EmbeddingsProvider, build_embeddings
from lanereach.assistant.knowledge import Chunk, all_chunks
from lanereach.assistant.models import HelpChunk
from lanereach.automation.nodes.registry import NodeRegistry
from lanereach.core.clock import Clock
from lanereach.core.container import ServiceContainer
from lanereach.core.tenancy import (
    open_system_session,
    open_tenant_session,
    require_session_tenant_id,
)

logger = structlog.get_logger(__name__)

type EmbeddingCache = dict[tuple[str, str], list[float]]


@dataclass(slots=True)
class IndexRun:
    """What a sync changed in one tenant."""

    added: int = 0
    updated: int = 0
    removed: int = 0
    embedded: int = 0


def _embedding_text(chunk: Chunk) -> str:
    return f"{chunk.title}\n{chunk.heading}\n{chunk.content}"


async def sync_help_index(
    session: AsyncSession,
    clock: Clock,
    registry: NodeRegistry,
    embeddings: EmbeddingsProvider | None,
    cache: EmbeddingCache | None = None,
) -> IndexRun:
    """Make the tenant's help index match the current articles.

    Args:
        session: Tenant session.
        clock: Time source.
        registry: Node registry (for the generated steps reference).
        embeddings: Embeddings provider, or None for text search only.
        cache: Embeddings already computed in this run, by (content hash, model).

    Returns:
        Counts of added, updated, removed and embedded sections.
    """
    now = clock.now()
    run = IndexRun()
    cache = cache if cache is not None else {}
    chunks = {chunk.key: chunk for chunk in all_chunks(registry)}
    rows = {
        row.chunk_key: row
        for row in (
            await session.scalars(select(HelpChunk).options(defer(HelpChunk.embedding)))
        ).all()
    }
    stale = [key for key in rows if key not in chunks]
    if stale:
        await session.execute(delete(HelpChunk).where(HelpChunk.chunk_key.in_(stale)))
        run.removed = len(stale)
    to_embed: list[tuple[HelpChunk, Chunk]] = []
    for key, chunk in chunks.items():
        row = rows.get(key)
        if row is None:
            row = HelpChunk(
                tenant_id=require_session_tenant_id(session),
                chunk_key=key,
                source=chunk.source,
                slug=chunk.slug,
                title=chunk.title[:200],
                heading=chunk.heading[:200],
                content=chunk.content,
                content_hash=chunk.content_hash,
                embedding=None,
                embedding_model=None,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            run.added += 1
        elif row.content_hash != chunk.content_hash:
            row.source, row.slug = chunk.source, chunk.slug
            row.title, row.heading = chunk.title[:200], chunk.heading[:200]
            row.content, row.content_hash = chunk.content, chunk.content_hash
            row.embedding, row.embedding_model = None, None
            row.updated_at = now
            run.updated += 1
        if embeddings is not None and row.embedding_model != embeddings.model:
            to_embed.append((row, chunk))
    if embeddings is not None and to_embed:
        missing = [
            chunk for _, chunk in to_embed if (chunk.content_hash, embeddings.model) not in cache
        ]
        if missing:
            vectors = await embeddings.embed([_embedding_text(chunk) for chunk in missing])
            for chunk, vector in zip(missing, vectors, strict=True):
                cache[(chunk.content_hash, embeddings.model)] = vector
        for row, chunk in to_embed:
            row.embedding = cache[(chunk.content_hash, embeddings.model)]
            row.embedding_model = embeddings.model
            row.updated_at = now
        run.embedded = len(to_embed)
    await session.flush()
    return run


async def ensure_help_index(
    session: AsyncSession,
    clock: Clock,
    registry: NodeRegistry,
    embeddings: EmbeddingsProvider | None,
) -> None:
    """Build the index on first use (the daily job keeps it current afterwards)."""
    count = await session.scalar(select(func.count()).select_from(HelpChunk))
    if not count:
        await sync_help_index(session, clock, registry, embeddings)


async def sync_help_index_in_all_tenants(container: ServiceContainer) -> dict[str, int]:
    """Refresh every tenant's help index (Celery beat, daily, and after deploys)."""
    embeddings = build_embeddings(container.settings)
    cache: EmbeddingCache = {}
    async with open_system_session(container.session_factory) as system:
        tenant_ids: list[uuid.UUID] = list(
            (await system.scalars(text("SELECT tenant_id FROM maintenance_list_tenants()"))).all()
        )
    totals = {"tenants": len(tenant_ids), "changed": 0, "embedded": 0, "errors": 0}
    for tenant_id in tenant_ids:
        try:
            async with open_tenant_session(container.session_factory, tenant_id) as session:
                run = await sync_help_index(
                    session, container.clock, container.node_registry, embeddings, cache
                )
        except Exception:
            logger.exception("help_index_sync_failed", tenant_id=str(tenant_id))
            totals["errors"] += 1
            continue
        totals["changed"] += run.added + run.updated + run.removed
        totals["embedded"] += run.embedded
    return totals
