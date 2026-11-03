"""Tenant-scoped database sessions and Row-Level Security helpers.

Isolation is enforced by PostgreSQL, not by `WHERE` clauses in application code:

* Every tenant-owned table has an RLS policy comparing `tenant_id` with the transaction-local
  setting `app.tenant_id`.
* The application connects as a role that does not own the tables and has no `BYPASSRLS`,
  so the policies always apply.
* `open_tenant_session` stores the tenant on the session and an `after_begin` listener runs
  `set_config('app.tenant_id', ..., is_local => true)` at the start of *every* transaction the
  session opens. The setting therefore survives commits and never leaks through the pool.

A session opened without a tenant sees no tenant rows at all (the policy compares against
NULL), so forgetting to scope a query fails closed.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import structlog
from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, SessionTransaction

TENANT_SETTING = "app.tenant_id"
_TENANT_INFO_KEY = "tenant_id"
_AFTER_COMMIT_KEY = "after_commit"

logger = structlog.get_logger(__name__)

# NULLIF turns an unset or reset setting ('') into NULL so the cast never errors and the
# comparison is never true.
_CURRENT_TENANT_SQL = f"NULLIF(current_setting('{TENANT_SETTING}', true), '')::uuid"


@event.listens_for(Session, "after_begin")
def _apply_tenant_setting(
    session: Session, _transaction: SessionTransaction, connection: Connection
) -> None:
    tenant_id = session.info.get(_TENANT_INFO_KEY)
    if tenant_id is not None:
        connection.execute(
            text("SELECT set_config(:setting, :tenant_id, true)"),
            {"setting": TENANT_SETTING, "tenant_id": str(tenant_id)},
        )


def session_tenant_id(session: AsyncSession) -> uuid.UUID | None:
    """Return the tenant a session is scoped to.

    Args:
        session: A session created by `open_tenant_session` or `open_system_session`.

    Returns:
        The tenant ID, or None for a system session.
    """
    tenant_id: Any = session.info.get(_TENANT_INFO_KEY)
    return tenant_id if isinstance(tenant_id, uuid.UUID) else None


def require_session_tenant_id(session: AsyncSession) -> uuid.UUID:
    """Return the session's tenant, failing loudly if it is a system session.

    Repositories that insert tenant-owned rows use this so a row can never be written for a
    tenant other than the one the session is scoped to.

    Args:
        session: A tenant-scoped session.

    Returns:
        The tenant ID.

    Raises:
        RuntimeError: If the session is not tenant-scoped (a programming error).
    """
    tenant_id = session_tenant_id(session)
    if tenant_id is None:
        raise RuntimeError("operation requires a tenant-scoped session")
    return tenant_id


def run_after_commit(session: AsyncSession, callback: Callable[[], Awaitable[None]]) -> None:
    """Schedule work to run once the session's transaction has committed.

    Used to enqueue background work (event processing, enrollment advances) only when the rows
    it needs are visible to other connections. Callbacks are dropped if the transaction rolls
    back. A failing callback is logged, not raised: the data is committed and the periodic
    dispatcher picks the work up anyway.

    Args:
        session: A session opened by `open_tenant_session`.
        callback: Coroutine function to await after commit.
    """
    session.info.setdefault(_AFTER_COMMIT_KEY, []).append(callback)


async def _run_after_commit_callbacks(session: AsyncSession) -> None:
    callbacks: list[Callable[[], Awaitable[None]]] = session.info.pop(_AFTER_COMMIT_KEY, [])
    for callback in callbacks:
        try:
            await callback()
        except Exception as exc:  # noqa: BLE001 - see run_after_commit
            logger.warning("after_commit_callback_failed", error=type(exc).__name__)


@asynccontextmanager
async def open_tenant_session(
    session_factory: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> AsyncIterator[AsyncSession]:
    """Open a session whose every transaction is scoped to `tenant_id`.

    The session commits when the block exits normally and rolls back on error. Used by API
    dependencies and by Celery workers (which take the tenant from the job payload).

    Args:
        session_factory: Factory bound to the application (non-owner) database role.
        tenant_id: The tenant whose rows the session may see.

    Yields:
        The tenant-scoped session.
    """
    async with session_factory(info={_TENANT_INFO_KEY: tenant_id}) as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            session.info.pop(_AFTER_COMMIT_KEY, None)
            await session.rollback()
            raise
        await _run_after_commit_callbacks(session)


@asynccontextmanager
async def open_system_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Open a session with no tenant context.

    RLS hides every tenant row from this session. It exists only to call the narrow
    `SECURITY DEFINER` lookup functions used before a tenant is known (for example resolving an
    API key prefix to its tenant during authentication).

    Args:
        session_factory: Factory bound to the application database role.

    Yields:
        The unscoped session.
    """
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


def tenant_rls_statements(table_name: str, *, tenant_column: str = "tenant_id") -> list[str]:
    """Build the SQL that enables tenant isolation on a table.

    Migrations call this for every tenant-owned table. `FORCE ROW LEVEL SECURITY` is not used
    on purpose: the owner role must bypass policies to run migrations and the `SECURITY DEFINER`
    auth lookup functions, while the application role is never the owner.

    Args:
        table_name: Table to protect.
        tenant_column: Column holding the tenant ID (`id` for the `tenants` table itself).

    Returns:
        SQL statements to execute in order.
    """
    predicate = f"{tenant_column} = {_CURRENT_TENANT_SQL}"
    return [
        f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY",
        f"CREATE POLICY tenant_isolation ON {table_name} "
        f"USING ({predicate}) WITH CHECK ({predicate})",
    ]
