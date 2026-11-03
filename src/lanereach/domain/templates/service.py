"""Template business logic and data access."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.core.clock import Clock
from lanereach.core.errors import BusinessRuleViolationError, NotFoundError
from lanereach.core.repository import TenantRepository, flush_or_conflict
from lanereach.core.tenancy import require_session_tenant_id
from lanereach.domain.senders.models import ServiceLine
from lanereach.domain.templates.models import Template, TemplateSource

EDITABLE_FIELDS = frozenset({"name", "subject", "body"})


class TemplateRepository(TenantRepository[Template]):
    """Queries over templates."""

    model = Template

    async def list_by_key(self, key: str | None) -> Sequence[Template]:
        """Templates ordered by key then language.

        Args:
            key: Only this key, when given.

        Returns:
            The templates.
        """
        statement = select(Template).order_by(Template.key, Template.language)
        if key:
            statement = statement.where(Template.key == key)
        return (await self.session.scalars(statement)).all()


class TemplateService:
    """Template operations.

    Args:
        session: Tenant session.
        clock: Time source.
    """

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock
        self.repository = TemplateRepository(session)

    async def create_template(
        self,
        *,
        key: str,
        language: str,
        name: str,
        subject: str,
        body: str,
        service_line: ServiceLine = ServiceLine.ANY,
    ) -> Template:
        """Create one language version of a template.

        Args:
            key: Template key (`intro`, `follow_up_1`...).
            language: ISO 639-1 language.
            name: Display name.
            subject: Subject with placeholders.
            body: Body with placeholders.
            service_line: Air or ocean agents, or any.

        Returns:
            The template.

        Raises:
            ConflictError: If the key/language pair exists.
        """
        now = self._clock.now()
        template = Template(
            tenant_id=require_session_tenant_id(self._session),
            key=key,
            language=language,
            name=name,
            subject=subject,
            body=body,
            service_line=service_line,
            created_at=now,
            updated_at=now,
        )
        self._session.add(template)
        await flush_or_conflict(self._session, f"Template '{key}' already exists in '{language}'.")
        return template

    async def require_template(self, template_id: uuid.UUID) -> Template:
        """Load a template or raise 404.

        Raises:
            NotFoundError: If it does not exist in the tenant.
        """
        template = await self.repository.get(template_id)
        if template is None:
            raise NotFoundError("Template not found.")
        return template

    async def update_template(self, template_id: uuid.UUID, changes: dict[str, Any]) -> Template:
        """Update the name, subject or body of a template version.

        Args:
            template_id: Template ID.
            changes: Fields to change (others are ignored).

        Returns:
            The updated template.
        """
        template = await self.require_template(template_id)
        if template.source is TemplateSource.SHAREPOINT:
            # SharePoint is the master copy: edit the Word document there instead.
            raise BusinessRuleViolationError(
                "This script comes from SharePoint; edit the Word document there."
            )
        for field, value in changes.items():
            if field in EDITABLE_FIELDS:
                setattr(template, field, value)
        template.updated_at = self._clock.now()
        return template

    async def delete_template(self, template_id: uuid.UUID) -> None:
        """Delete a template version.

        Raises:
            NotFoundError: If missing.
        """
        await self.repository.delete(await self.require_template(template_id))
