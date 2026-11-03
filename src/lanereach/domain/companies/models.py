"""Company, branch and office-rotation tables."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.core.database import (
    Base,
    CreatedAtMixin,
    TenantScopedMixin,
    UUIDPrimaryKeyMixin,
    tenant_foreign_key,
    tenant_unique_id,
)


class Company(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """A prospect company. Freezing it blocks outreach to every branch and contact."""

    __tablename__ = "companies"
    __table_args__ = (
        tenant_unique_id(),
        Index("ix_companies_tenant_id_domain", "tenant_id", "domain"),
    )

    name: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str | None] = mapped_column(String(253))
    country: Mapped[str | None] = mapped_column(String(2))
    industry: Mapped[str | None] = mapped_column(String(100))
    frozen_at: Mapped[datetime | None]
    frozen_reason: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime]


class CompanyBranch(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """A location of a company (for example its Dubai branch)."""

    __tablename__ = "company_branches"
    __table_args__ = (tenant_unique_id(), tenant_foreign_key("company_id", "companies"))

    company_id: Mapped[uuid.UUID]
    name: Mapped[str] = mapped_column(String(200))
    country: Mapped[str] = mapped_column(String(2))
    city: Mapped[str | None] = mapped_column(String(100))


class CompanyOfficeStart(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """Which office started outreach to a company in an ISO week (rotation guardrail).

    The unique key makes "first office wins the week" race-free.
    """

    __tablename__ = "company_office_starts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "company_id", "iso_year", "iso_week"),
        tenant_foreign_key("company_id", "companies"),
        tenant_foreign_key("office_id", "offices"),
    )

    company_id: Mapped[uuid.UUID]
    office_id: Mapped[uuid.UUID]
    iso_year: Mapped[int] = mapped_column(Integer)
    iso_week: Mapped[int] = mapped_column(Integer)
