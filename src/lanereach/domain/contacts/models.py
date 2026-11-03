"""Contact table and funnel stages."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.core.database import (
    Base,
    CreatedAtMixin,
    StrEnumType,
    TenantScopedMixin,
    UUIDPrimaryKeyMixin,
    tenant_foreign_key,
    tenant_unique_id,
)


class ContactStage(StrEnum):
    """Where a contact is in the outreach funnel."""

    NEW = "new"
    CONTACTED = "contacted"
    ENGAGED = "engaged"
    QUALIFIED = "qualified"
    CUSTOMER = "customer"
    PARTNER = "partner"
    NOT_INTERESTED = "not_interested"
    DO_NOT_CONTACT = "do_not_contact"


class Contact(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """A person at a prospect company.

    `email` is stored lower-cased and is unique per tenant. `office_id` is the office that owns
    the relationship (used for routing replies and notifying the right owner).
    """

    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email"),
        tenant_unique_id(),
        tenant_foreign_key("company_id", "companies", nullable_column=True),
        tenant_foreign_key("branch_id", "company_branches", nullable_column=True),
        tenant_foreign_key("office_id", "offices", nullable_column=True),
    )

    company_id: Mapped[uuid.UUID | None]
    branch_id: Mapped[uuid.UUID | None]
    office_id: Mapped[uuid.UUID | None]
    email: Mapped[str] = mapped_column(String(320))
    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    job_title: Mapped[str | None] = mapped_column(String(200))
    country: Mapped[str | None] = mapped_column(String(2))
    city: Mapped[str | None] = mapped_column(String(100))
    language: Mapped[str | None] = mapped_column(String(2))
    timezone: Mapped[str | None] = mapped_column(String(64))
    stage: Mapped[ContactStage] = mapped_column(
        StrEnumType(ContactStage, 30), default=ContactStage.NEW
    )
    owner_email: Mapped[str | None] = mapped_column(String(320))
    # Forwarder network the contact belongs to (WCA, JCtrans...), used in script footers.
    network: Mapped[str | None] = mapped_column(String(50))
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    # Result of email verification (`integrations.verification`): invalid addresses are blocked.
    email_status: Mapped[str] = mapped_column(
        String(20), default="unknown", server_default="unknown"
    )
    email_verified_at: Mapped[datetime | None]
    updated_at: Mapped[datetime]

    @property
    def email_domain(self) -> str:
        """The part of the email address after `@`."""
        return self.email.rsplit("@", 1)[-1]

    @property
    def display_name(self) -> str:
        """First and last name, or the email address when no name is known."""
        name = " ".join(part for part in (self.first_name, self.last_name) if part)
        return name or self.email
