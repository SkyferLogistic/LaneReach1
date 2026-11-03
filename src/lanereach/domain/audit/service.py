"""Recording and reading the audit log.

`record_audit` is called by the services of the actions that matter for accountability:
sending controls, publishing and pausing campaigns, branding and library settings, API keys,
suppressions, agents and offices, imports and deal moves. It writes in the same transaction as
the change, so a rolled-back change leaves no audit row.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.core.clock import Clock
from lanereach.core.errors import BusinessRuleViolationError
from lanereach.core.tenancy import require_session_tenant_id
from lanereach.domain.audit.models import AuditEvent

# Keys that must never reach the audit log even if a caller passes them.
_SECRET_KEYS = ("secret", "password", "token", "api_key", "key_hash", "body", "html")


def _clean(changes: dict[str, Any] | None) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in (changes or {}).items():
        if any(part in key.lower() for part in _SECRET_KEYS):
            continue
        cleaned[key] = value if isinstance(value, str | int | float | bool | None) else str(value)
    return cleaned


def record_audit(
    session: AsyncSession,
    clock: Clock,
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | str | None,
    summary: str,
    changes: dict[str, Any] | None = None,
) -> AuditEvent:
    """Add an audit row to the current transaction.

    Args:
        session: Tenant session.
        clock: Time source.
        actor: Who did it (principal subject).
        action: Dotted verb, for example `workflow.published`.
        entity_type: What kind of thing changed, for example `workflow`.
        entity_id: Which one.
        summary: One readable sentence.
        changes: Changed fields; secret-looking keys and email bodies are dropped.

    Returns:
        The pending row.
    """
    now = clock.now()
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    event = AuditEvent(
        tenant_id=require_session_tenant_id(session),
        actor=actor[:200],
        action=action[:80],
        entity_type=entity_type[:40],
        entity_id=str(entity_id)[:100] if entity_id is not None else None,
        summary=summary[:500],
        changes=_clean(changes),
        request_id=str(request_id)[:100] if request_id else None,
        occurred_at=now,
        created_at=now,
    )
    session.add(event)
    return event


@dataclass(frozen=True, slots=True)
class AuditPage:
    """A page of the audit log."""

    items: list[AuditEvent]
    next_cursor: str | None


async def list_audit_events(
    session: AsyncSession,
    *,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> AuditPage:
    """The audit log, newest first.

    Args:
        session: Tenant session.
        action: Actions starting with this text (`workflow.` matches every workflow action).
        entity_type: Only this kind of entity.
        entity_id: Only this entity.
        actor: Only this person (case-insensitive).
        limit: Page size.
        cursor: `next_cursor` of the previous page.

    Returns:
        The page.

    Raises:
        BusinessRuleViolationError: For a cursor this function did not issue.
    """
    statement = select(AuditEvent)
    if action:
        statement = statement.where(AuditEvent.action.startswith(action, autoescape=True))
    if entity_type:
        statement = statement.where(AuditEvent.entity_type == entity_type)
    if entity_id:
        statement = statement.where(AuditEvent.entity_id == entity_id)
    if actor:
        statement = statement.where(func.lower(AuditEvent.actor) == actor.strip().lower())
    if cursor:
        try:
            stamp, row_id = cursor.split("|", 1)
            at, last_id = datetime.fromisoformat(stamp), uuid.UUID(row_id)
        except ValueError as exc:
            raise BusinessRuleViolationError("Invalid cursor.") from exc
        statement = statement.where(
            or_(
                AuditEvent.occurred_at < at,
                and_(AuditEvent.occurred_at == at, AuditEvent.id < last_id),
            )
        )
    rows = list(
        (
            await session.scalars(
                statement.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc()).limit(
                    limit + 1
                )
            )
        ).all()
    )
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = f"{rows[-1].occurred_at.isoformat()}|{rows[-1].id}" if more and rows else None
    return AuditPage(rows, next_cursor)


_AUDITED_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Frequent, low-stakes or read-like requests that would drown the log.
_UNAUDITED_PATHS = (
    "/inbox/",
    "/notifications",
    "/ai/replies/",
    "/ai/runs",
    "/events",
    "/crm/tasks",
    "/crm/notes",
    "/dashboard",
)
_UNAUDITED_SUFFIXES = ("/test-run", "/validate", "/validate-graph", "/preview")
_MAX_CHANGED_FIELDS = 20


def _entity(path: str, path_params: dict[str, Any]) -> tuple[str, str | None]:
    # The entity is the collection before the first path parameter (`/workflows/{id}/publish`
    # → `workflow`), else the last segment (`/settings/sending` → `sending`).
    parts = [part for part in path.split("/") if part]
    entity_type = parts[-1] if parts else "tenant"
    for index, part in enumerate(parts):
        if part.startswith("{") and index > 0:
            entity_type = parts[index - 1]
            break
    entity_id = next((str(value) for key, value in path_params.items() if key.endswith("id")), None)
    return entity_type.removesuffix("s"), entity_id


async def record_request_audit(
    session: AsyncSession,
    clock: Clock,
    *,
    actor: str,
    method: str,
    route_path: str | None,
    route_name: str | None,
    route_summary: str | None,
    path_params: dict[str, Any],
    json_body: Any,
) -> AuditEvent | None:
    """Audit a successful change made through the API (called before the request commits).

    Every create, update, delete and action endpoint is recorded with its route summary and the
    top-level fields sent, except the frequent ones listed in `_UNAUDITED_PATHS`.

    Args:
        session: The request's tenant session.
        clock: Time source.
        actor: Principal subject.
        method: HTTP method.
        route_path: Route template, for example `/api/v1/automation/workflows/{workflow_id}`.
        route_name: Endpoint function name, used as the action.
        route_summary: The endpoint's summary, used as the readable summary.
        path_params: Path parameters (the entity id comes from here).
        json_body: Parsed JSON body, if any.

    Returns:
        The pending row, or None when the request is not audited.
    """
    if method not in _AUDITED_METHODS or not route_path or not route_name:
        return None
    relative = route_path.removeprefix("/api/v1")
    if relative.startswith(_UNAUDITED_PATHS) or relative.endswith(_UNAUDITED_SUFFIXES):
        return None
    entity_type, entity_id = _entity(relative, path_params)
    changes: dict[str, Any] = {}
    if isinstance(json_body, dict):
        scalars = {k: v for k, v in json_body.items() if not isinstance(v, dict | list)}
        changes = dict(list(scalars.items())[:_MAX_CHANGED_FIELDS])
        for key, value in json_body.items():
            if isinstance(value, list):
                short = len(value) <= 10 and all(isinstance(item, str | int) for item in value)
                changes[key] = ", ".join(map(str, value)) if short else f"{len(value)} items"
    return record_audit(
        session,
        clock,
        actor=actor,
        action=route_name,
        entity_type=entity_type,
        entity_id=entity_id,
        summary=route_summary or f"{method} {relative}",
        changes=changes,
    )
