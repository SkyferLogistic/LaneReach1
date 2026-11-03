"""Search index of the help articles (text and pgvector embedding per section).

Kept per tenant like every table (RLS): the content is the same product help for everyone, but
tenant scoping keeps the "every table has tenant_id" rule without exceptions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Index, String, Text, UniqueConstraint, cast
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.types import UserDefinedType

from lanereach.assistant.embeddings import DIMENSIONS
from lanereach.core.database import Base, CreatedAtMixin, TenantScopedMixin, UUIDPrimaryKeyMixin


class Vector(UserDefinedType[list[float]]):
    """PostgreSQL `vector(n)` (pgvector) sent and read as text, so no extra driver is needed."""

    cache_ok = True

    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **kw: Any) -> str:
        """The column type."""
        return f"vector({self.dimensions})"

    def bind_processor(self, dialect: Any) -> Any:
        """`[0.1,0.2,...]` text for pgvector's input function."""

        def process(value: list[float] | None) -> str | None:
            if value is None:
                return None
            return "[" + ",".join(f"{number:.7f}" for number in value) + "]"

        return process

    def bind_expression(self, bindvalue: Any) -> ColumnElement[Any]:
        """Send the parameter as text and let PostgreSQL cast it."""
        return cast(cast(bindvalue, Text), self)

    def column_expression(self, column: Any) -> ColumnElement[Any]:
        """Read the vector back as text."""
        return cast(column, Text)

    def result_processor(self, dialect: Any, coltype: Any) -> Any:
        """Parse `[0.1,0.2,...]`."""

        def process(value: str | None) -> list[float] | None:
            if value is None:
                return None
            return [float(item) for item in value.strip("[]").split(",") if item]

        return process


class HelpChunk(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """One section of a help article, searchable by text and by meaning."""

    __tablename__ = "help_chunks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "chunk_key"),
        # Approximate nearest-neighbour search by cosine distance (pgvector HNSW).
        Index(
            "ix_help_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    chunk_key: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(20))
    slug: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(200))
    heading: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(DIMENSIONS))
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime]
