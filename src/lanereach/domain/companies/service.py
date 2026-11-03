"""Company management."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.core.clock import Clock
from lanereach.core.errors import ConflictError, NotFoundError
from lanereach.core.repository import TenantRepository
from lanereach.core.tenancy import require_session_tenant_id
from lanereach.domain.companies.models import Company, CompanyBranch
from lanereach.domain.companies.schemas import (
    BranchCreateRequest,
    BranchResponse,
    CompanyCreateRequest,
    CompanyResponse,
    CompanyUpdateRequest,
)
from lanereach.domain.contacts.models import Contact


class CompanyRepository(TenantRepository[Company]):
    """Queries over companies."""

    model = Company

    async def search(
        self, query: str | None, *, limit: int, cursor: str | None
    ) -> tuple[Sequence[Company], str | None]:
        """Companies newest first, optionally filtered by name or domain."""
        statement = select(Company)
        if query:
            pattern = f"%{query.replace('%', '').replace('_', '')}%"
            statement = statement.where(Company.name.ilike(pattern) | Company.domain.ilike(pattern))
        return await self.page(statement, limit=limit, cursor=cursor)

    async def branches_of(
        self, company_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[CompanyBranch]]:
        """Branches grouped by company."""
        grouped: dict[uuid.UUID, list[CompanyBranch]] = {
            company_id: [] for company_id in company_ids
        }
        if company_ids:
            rows = await self.session.scalars(
                select(CompanyBranch)
                .where(CompanyBranch.company_id.in_(company_ids))
                .order_by(CompanyBranch.name)
            )
            for branch in rows:
                grouped[branch.company_id].append(branch)
        return grouped

    async def contact_counts(self, company_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
        """Number of contacts per company."""
        if not company_ids:
            return {}
        rows = await self.session.execute(
            select(Contact.company_id, func.count())
            .where(Contact.company_id.in_(company_ids))
            .group_by(Contact.company_id)
        )
        return {company_id: count for company_id, count in rows.all() if company_id is not None}


class CompanyService:
    """Company operations.

    Args:
        session: Tenant session.
        clock: Time source.
    """

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock
        self.repository = CompanyRepository(session)

    async def create_company(self, request: CompanyCreateRequest) -> Company:
        """Create a company with its branches."""
        now = self._clock.now()
        tenant_id = require_session_tenant_id(self._session)
        company = Company(
            tenant_id=tenant_id,
            name=request.name,
            domain=request.domain.lower() if request.domain else None,
            country=request.country,
            industry=request.industry,
            created_at=now,
            updated_at=now,
        )
        self._session.add(company)
        await self._session.flush()
        for branch in request.branches:
            await self.add_branch(company.id, branch)
        return company

    async def add_branch(
        self, company_id: uuid.UUID, request: BranchCreateRequest
    ) -> CompanyBranch:
        """Add a branch to a company.

        Raises:
            NotFoundError: If the company does not exist.
        """
        await self.require_company(company_id)
        branch = CompanyBranch(
            tenant_id=require_session_tenant_id(self._session),
            company_id=company_id,
            name=request.name,
            country=request.country,
            city=request.city,
            created_at=self._clock.now(),
        )
        self._session.add(branch)
        await self._session.flush()
        return branch

    async def require_company(self, company_id: uuid.UUID) -> Company:
        """Load a company or raise 404."""
        company = await self.repository.get(company_id)
        if company is None:
            raise NotFoundError("Company not found.")
        return company

    async def update_company(self, company_id: uuid.UUID, request: CompanyUpdateRequest) -> Company:
        """Update a company; `frozen` toggles the company freeze.

        Raises:
            NotFoundError: If missing.
        """
        company = await self.require_company(company_id)
        changes = request.model_dump(exclude_unset=True)
        frozen = changes.pop("frozen", None)
        reason = changes.pop("frozen_reason", None)
        for field, value in changes.items():
            setattr(company, field, value)
        now = self._clock.now()
        if frozen is True:
            company.frozen_at = company.frozen_at or now
            company.frozen_reason = reason or company.frozen_reason or "frozen manually"
        elif frozen is False:
            company.frozen_at = None
            company.frozen_reason = None
        company.updated_at = now
        return company

    async def delete_company(self, company_id: uuid.UUID) -> None:
        """Delete a company that has no contacts left (branches and office history go with it).

        Contacts are never orphaned silently: without their company the company freeze and
        rotation rules would stop protecting them.

        Raises:
            NotFoundError: If missing.
            ConflictError: While contacts belong to it.
        """
        company = await self.require_company(company_id)
        counts = await self.repository.contact_counts([company.id])
        if counts.get(company.id):
            raise ConflictError(
                f"{company.name} still has {counts[company.id]} contact(s). "
                "Move or delete them first."
            )
        await self._session.delete(company)
        await self._session.flush()

    async def describe(self, companies: Sequence[Company]) -> list[CompanyResponse]:
        """Render companies with branches and contact counts."""
        ids = [company.id for company in companies]
        branches = await self.repository.branches_of(ids)
        counts = await self.repository.contact_counts(ids)
        return [
            CompanyResponse(
                id=company.id,
                name=company.name,
                domain=company.domain,
                country=company.country,
                industry=company.industry,
                frozen_at=company.frozen_at,
                frozen_reason=company.frozen_reason,
                branches=[BranchResponse.model_validate(branch) for branch in branches[company.id]],
                contact_count=counts.get(company.id, 0),
                created_at=company.created_at,
                updated_at=company.updated_at,
            )
            for company in companies
        ]
