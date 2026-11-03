"""Cursor pagination.

List endpoints return items newest first, ordered by `(created_at DESC, id DESC)`. The cursor
is an opaque URL-safe token encoding the last item's sort key, so pages stay stable while new
rows are inserted (unlike offset pagination).
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field
from sqlalchemy import Select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from lanereach.core.errors import LaneReachError

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class InvalidCursorError(LaneReachError):
    """The `cursor` query parameter could not be decoded."""

    status_code = 400
    problem_type = "invalid-cursor"
    title = "Invalid pagination cursor"


class Page[ItemT](BaseModel):
    """One page of a list response."""

    items: list[ItemT]
    next_cursor: str | None = Field(
        default=None, description="Pass as `cursor` to fetch the next page; null on the last page."
    )


@dataclass(frozen=True, slots=True)
class CursorPosition:
    """Sort key of the last item on a page."""

    created_at: datetime
    id: uuid.UUID


def encode_cursor(position: CursorPosition) -> str:
    """Encode a cursor position as an opaque token.

    Args:
        position: Sort key of the last returned item.

    Returns:
        URL-safe token.
    """
    raw = json.dumps({"c": position.created_at.isoformat(), "i": str(position.id)})
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> CursorPosition:
    """Decode a token produced by `encode_cursor`.

    Args:
        cursor: The token.

    Returns:
        The encoded position.

    Raises:
        InvalidCursorError: If the token is not a valid cursor.
    """
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        created_at = datetime.fromisoformat(data["c"])
        if created_at.tzinfo is None:
            raise ValueError("naive timestamp")
        return CursorPosition(created_at=created_at, id=uuid.UUID(data["i"]))
    except (ValueError, KeyError, TypeError, binascii.Error) as exc:
        raise InvalidCursorError("The cursor is malformed.") from exc


class _Paginatable(Protocol):
    id: Any
    created_at: Any


async def paginate_newest_first[RowT: _Paginatable](
    session: AsyncSession,
    statement: Select[RowT],
    *,
    created_at_column: InstrumentedAttribute[datetime],
    id_column: InstrumentedAttribute[uuid.UUID],
    limit: int,
    cursor: str | None,
) -> tuple[Sequence[RowT], str | None]:
    """Run `statement` as a newest-first keyset page.

    Args:
        session: Session to execute in.
        statement: A `select(Model)` with any filters applied, but no ordering or limit.
        created_at_column: The model's `created_at` column.
        id_column: The model's `id` column (tie-breaker).
        limit: Page size, clamped to `1..MAX_PAGE_SIZE`.
        cursor: Token from the previous page, or None for the first page.

    Returns:
        The rows of this page and the cursor of the next page (None if this is the last).

    Raises:
        InvalidCursorError: If `cursor` is malformed.
    """
    page_size = max(1, min(limit, MAX_PAGE_SIZE))
    if cursor is not None:
        position = decode_cursor(cursor)
        statement = statement.where(
            or_(
                created_at_column < position.created_at,
                and_(created_at_column == position.created_at, id_column < position.id),
            )
        )
    statement = statement.order_by(created_at_column.desc(), id_column.desc()).limit(page_size + 1)
    rows = list((await session.scalars(statement)).all())
    if len(rows) <= page_size:
        return rows, None
    rows = rows[:page_size]
    last = rows[-1]
    return rows, encode_cursor(CursorPosition(created_at=last.created_at, id=last.id))
