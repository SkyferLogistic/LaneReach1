"""Leads: the pricing team's progress (which can start workflows) and the CSV export."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.automation.engine.queue import TaskQueue
from lanereach.automation.events.models import EventType
from lanereach.automation.events.service import publish_event
from lanereach.core.clock import Clock
from lanereach.core.csv_files import write_csv
from lanereach.core.errors import NotFoundError
from lanereach.domain.companies.models import Company
from lanereach.domain.contacts.models import Contact
from lanereach.domain.leads.models import Lead, LeadPriority, LeadStatus

MAX_EXPORT_ROWS = 10_000
EXPORT_COLUMNS = (
    "created_at",
    "status",
    "priority",
    "contact",
    "email",
    "company",
    "contact_country",
    "summary",
    "mode",
    "origin",
    "destination",
    "container",
    "commodity",
    "weight_kg",
    "volume_cbm",
    "incoterm",
    "cargo_ready_date",
    "frequency",
    "hazardous",
)


async def update_lead(
    session: AsyncSession,
    clock: Clock,
    task_queue: TaskQueue | None,
    lead_id: uuid.UUID,
    *,
    status: LeadStatus | None = None,
    priority: LeadPriority | None = None,
) -> Lead:
    """Change a lead's status or priority.

    A status change publishes `lead.status_changed`, so workflows can follow up on won or lost
    deals (for example: tag won customers, re-engage lost ones in three months).

    Args:
        session: Tenant session.
        clock: Time source.
        task_queue: Where the event's processing is enqueued after commit.
        lead_id: The lead.
        status: New status.
        priority: New priority.

    Returns:
        The lead.

    Raises:
        NotFoundError: If the lead does not exist in the tenant.
    """
    lead = await session.get(Lead, lead_id)
    if lead is None:
        raise NotFoundError("Lead not found.")
    previous = lead.status
    if priority is not None:
        lead.priority = priority
    if status is not None:
        lead.status = status
    lead.updated_at = clock.now()
    if status is not None and status is not previous:
        await publish_event(
            session,
            clock,
            event_type=EventType.LEAD_STATUS_CHANGED,
            contact_id=lead.contact_id,
            payload={"lead_id": str(lead.id), "status": status.value, "previous": previous.value},
            idempotency_key=f"lead:{lead.id}:{previous.value}->{status.value}:{lead.updated_at}",
            task_queue=task_queue,
        )
    return lead


def _place(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    parts = [value.get("city"), value.get("port_code"), value.get("country")]
    return " ".join(str(part) for part in parts if part)


async def export_leads_csv(session: AsyncSession, status: LeadStatus | None = None) -> bytes:
    """Leads with their contact and extracted shipment, newest first, as CSV.

    Args:
        session: Tenant session.
        status: Only leads in this status.

    Returns:
        The CSV file (at most `MAX_EXPORT_ROWS` rows).
    """
    statement = (
        select(Lead, Contact, Company)
        .outerjoin(Contact, Contact.id == Lead.contact_id)
        .outerjoin(Company, Company.id == Lead.company_id)
        .order_by(Lead.created_at.desc())
        .limit(MAX_EXPORT_ROWS)
    )
    if status is not None:
        statement = statement.where(Lead.status == status)
    rows = []
    for lead, contact, company in await session.execute(statement):
        shipment = lead.shipment or {}
        container = " x ".join(
            str(part)
            for part in (shipment.get("container_quantity"), shipment.get("container_type"))
            if part
        )
        rows.append(
            (
                lead.created_at.isoformat(),
                lead.status.value,
                lead.priority.value,
                contact.display_name if contact else "",
                contact.email if contact else "",
                company.name if company else "",
                contact.country if contact else "",
                lead.summary or "",
                shipment.get("mode") or "",
                _place(shipment.get("origin")),
                _place(shipment.get("destination")),
                container,
                shipment.get("commodity") or "",
                shipment.get("weight_kg") or "",
                shipment.get("volume_cbm") or "",
                shipment.get("incoterm") or "",
                shipment.get("cargo_ready_date") or "",
                shipment.get("frequency") or "",
                "" if shipment.get("is_hazardous") is None else shipment["is_hazardous"],
            )
        )
    return write_csv(EXPORT_COLUMNS, rows)
