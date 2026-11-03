"""Finding the help sections that answer a question: vectors and full-text, fused by rank.

Full-text search (PostgreSQL `tsvector`, words OR-ed so a natural question still matches) always
runs. When embeddings are configured, a pgvector cosine search runs too, and both rankings are
merged with reciprocal-rank fusion: a section found by both ranks first.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from lanereach.assistant.embeddings import EmbeddingsProvider
from lanereach.assistant.models import HelpChunk

CANDIDATES = 8
RRF_K = 60
_MAX_QUERY = 500

_FULL_TEXT = text(
    """
    SELECT chunk_key, ts_rank(document, query) AS rank
    FROM help_chunks,
         LATERAL (
            SELECT to_tsvector('english', title || ' ' || heading || ' ' || content) AS document,
                   NULLIF(
                       replace(plainto_tsquery('english', :question)::text, '&', '|'), ''
                   )::tsquery AS query
         ) AS search
    WHERE query IS NOT NULL AND document @@ query
    ORDER BY rank DESC
    LIMIT :limit
    """
)

_VECTOR = text(
    """
    SELECT chunk_key, 1 - (embedding <=> CAST(CAST(:vector AS text) AS vector)) AS similarity
    FROM help_chunks
    WHERE embedding IS NOT NULL AND embedding_model = :model
    ORDER BY embedding <=> CAST(CAST(:vector AS text) AS vector)
    LIMIT :limit
    """
)


@dataclass(frozen=True)
class SearchHit:
    """A help section and why it was found."""

    key: str
    slug: str
    title: str
    heading: str
    content: str
    score: float
    by_text: bool
    by_meaning: bool

    @property
    def url(self) -> str:
        """Where the console shows the article."""
        return f"/help/{self.slug}"


@dataclass(frozen=True)
class SearchResult:
    """The hits and which search ran."""

    hits: list[SearchHit]
    mode: str  # "hybrid" or "full_text"


def _vector_text(vector: list[float]) -> str:
    return "[" + ",".join(f"{number:.7f}" for number in vector) + "]"


async def search_help(
    session: AsyncSession,
    question: str,
    embeddings: EmbeddingsProvider | None,
    *,
    limit: int = 5,
) -> SearchResult:
    """The help sections most likely to answer the question.

    Args:
        session: Tenant session.
        question: The question (plain text; never interpolated into SQL).
        embeddings: Embeddings provider, or None for full-text search only.
        limit: How many sections to return.

    Returns:
        Ranked hits and the search mode.
    """
    question = question.strip()[:_MAX_QUERY]
    ranks: dict[str, float] = {}
    by_text: set[str] = set()
    by_meaning: set[str] = set()
    text_rows = (
        await session.execute(_FULL_TEXT, {"question": question, "limit": CANDIDATES})
    ).all()
    for position, (key, _rank) in enumerate(text_rows):
        ranks[key] = ranks.get(key, 0.0) + 1.0 / (RRF_K + position + 1)
        by_text.add(key)
    mode = "full_text"
    if embeddings is not None:
        mode = "hybrid"
        [vector] = await embeddings.embed([question])
        vector_rows = (
            await session.execute(
                _VECTOR,
                {"vector": _vector_text(vector), "model": embeddings.model, "limit": CANDIDATES},
            )
        ).all()
        for position, (key, similarity) in enumerate(vector_rows):
            # Unrelated sections still have a nearest neighbour; ignore the ones barely alike.
            if similarity is None or float(similarity) < 0.1:
                continue
            ranks[key] = ranks.get(key, 0.0) + 1.0 / (RRF_K + position + 1)
            by_meaning.add(key)
    ordered = sorted(ranks, key=lambda key: ranks[key], reverse=True)[:limit]
    if not ordered:
        return SearchResult([], mode)
    rows = {
        row.chunk_key: row
        for row in (
            await session.scalars(
                select(HelpChunk)
                .options(defer(HelpChunk.embedding))
                .where(HelpChunk.chunk_key.in_(ordered))
            )
        ).all()
    }
    hits = [
        SearchHit(
            key=key,
            slug=rows[key].slug,
            title=rows[key].title,
            heading=rows[key].heading,
            content=rows[key].content,
            score=round(ranks[key], 5),
            by_text=key in by_text,
            by_meaning=key in by_meaning,
        )
        for key in ordered
        if key in rows
    ]
    return SearchResult(hits, mode)
