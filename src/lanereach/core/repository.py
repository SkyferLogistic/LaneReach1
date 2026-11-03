"""Shared building blocks for tenant-scoped repositories."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Protocol

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from lanereach.core.errors import ConflictError
from lanereach.core.pagination import paginate_newest_first
from lanereach.core.tenancy import require_session_tenant_id


class TenantRow(Protocol):
    """What every tenant-owned ORM model provides."""

    id: Any
    tenant_id: Any
    created_at: Any


class TenantRepository[ModelT: TenantRow]:
    """Get/add/list helpers for one tenant-owned model.

    Subclasses set `model` and add query methods of their own. RLS already limits every
    statement to the session's tenant; `add` sets `tenant_id` from the session so rows can never
    be written for another tenant.

    Args:
        session: A tenant-scoped session.
    """

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, row_id: uuid.UUID, *, for_update: bool = False) -> ModelT | None:
        """Load a row by ID.

        Args:
            row_id: Primary key.
            for_update: Lock the row for the rest of the transaction.

        Returns:
            The row, or None when missing or owned by another tenant.
        """
        statement: Select[Any] = select(self.model).where(self._column("id") == row_id)
        if for_update:
            statement = statement.with_for_update()
        row: ModelT | None = await self.session.scalar(statement)
        return row

    async def add(self, row: ModelT) -> ModelT:
        """Insert a row for the session's tenant.

        Args:
            row: The new row.

        Returns:
            The flushed row.
        """
        row.tenant_id = require_session_tenant_id(self.session)
        self.session.add(row)
        await self.session.flush()
        return row

    async def delete(self, row: ModelT) -> None:
        """Delete a row.

        Args:
            row: A row loaded through this repository.
        """
        await self.session.delete(row)
        await self.session.flush()

    async def page(
        self, statement: Select[Any] | None = None, *, limit: int, cursor: str | None
    ) -> tuple[Sequence[ModelT], str | None]:
        """Run a newest-first page over `statement` (default: all rows).

        Args:
            statement: A `select(model)` with filters, or None for every row.
            limit: Page size.
            cursor: Cursor from the previous page.

        Returns:
            The rows and the next cursor.
        """
        rows, next_cursor = await paginate_newest_first(
            self.session,
            statement if statement is not None else select(self.model),
            created_at_column=self._column("created_at"),
            id_column=self._column("id"),
            limit=limit,
            cursor=cursor,
        )
        return rows, next_cursor

    def _column(self, name: str) -> InstrumentedAttribute[Any]:
        column: InstrumentedAttribute[Any] = getattr(self.model, name)
        return column


async def flush_or_conflict(session: AsyncSession, message: str) -> None:
    """Flush pending changes, turning a unique-constraint violation into a 409.

    Runs in a savepoint so the surrounding transaction stays usable after the conflict.

    Args:
        session: Tenant session with pending changes.
        message: Client-facing conflict message.

    Raises:
        ConflictError: If a unique constraint is violated.
    """
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        raise ConflictError(message) from exc
