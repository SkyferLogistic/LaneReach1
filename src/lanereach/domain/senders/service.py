"""Agents: bulk import from CSV and signature previews."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.core.clock import Clock
from lanereach.core.config import Settings
from lanereach.core.csv_files import read_csv_rows
from lanereach.core.tenancy import require_session_tenant_id
from lanereach.domain.assets.service import asset_url
from lanereach.domain.offices.models import Office
from lanereach.domain.senders.models import Sender, ServiceLine
from lanereach.domain.senders.signature import build_signature_html, build_signature_text
from lanereach.tenants.branding import load_branding
from lanereach.tenants.models import Tenant

MAX_IMPORT_ROWS = 500
REQUIRED_COLUMNS = ("name", "email", "office", "service_line")


@dataclass
class AgentImportSummary:
    """What a CSV import did."""

    created: int = 0
    updated: int = 0
    errors: list[str] = field(default_factory=list)


async def import_agents_csv(
    session: AsyncSession, clock: Clock, content: bytes
) -> AgentImportSummary:
    """Create or update agents from a CSV file (matched by email).

    Columns: `name, email, office, service_line` (air/ocean) and optionally `title, phone,
    mobile` (WhatsApp) and `daily_cap`. The office is matched by name. Rows with problems are
    reported, the others imported.

    Args:
        session: Tenant session.
        clock: Time source.
        content: CSV bytes (UTF-8, comma or semicolon separated).

    Returns:
        Counts and row errors.

    Raises:
        BusinessRuleViolationError: When the file is not a CSV with the required columns.
    """
    rows = read_csv_rows(content, REQUIRED_COLUMNS, max_rows=MAX_IMPORT_ROWS)
    offices = {
        office.name.strip().lower(): office
        for office in (await session.scalars(select(Office))).all()
    }
    summary = AgentImportSummary()
    now = clock.now()
    for number, row in rows:
        try:
            email = validate_email(row["email"], check_deliverability=False).normalized.lower()
        except EmailNotValidError:
            summary.errors.append(f"Row {number}: invalid email {row['email']!r}")
            continue
        office = offices.get(row["office"].lower())
        if office is None:
            summary.errors.append(f"Row {number}: unknown office {row['office']!r}")
            continue
        try:
            line = ServiceLine(row["service_line"].lower())
        except ValueError:
            summary.errors.append(f"Row {number}: service_line must be air, ocean or any")
            continue
        cap_text = row.get("daily_cap") or ""
        if cap_text and not (cap_text.isdigit() and 1 <= int(cap_text) <= 2000):
            summary.errors.append(f"Row {number}: daily_cap must be 1-2000")
            continue
        if not row["name"]:
            summary.errors.append(f"Row {number}: name is empty")
            continue
        agent = await session.scalar(select(Sender).where(func.lower(Sender.email) == email))
        if agent is None:
            agent = Sender(
                tenant_id=require_session_tenant_id(session),
                email=email,
                is_active=True,
                created_at=now,
            )
            session.add(agent)
            summary.created += 1
        else:
            summary.updated += 1
        agent.office_id = office.id
        agent.display_name = row["name"][:200]
        agent.service_line = line
        agent.job_title = row.get("title") or agent.job_title
        agent.phone = row.get("phone") or agent.phone
        agent.mobile = row.get("mobile") or row.get("whatsapp") or agent.mobile
        agent.daily_cap = int(cap_text) if cap_text else (agent.daily_cap or 50)
        agent.updated_at = now
    await session.flush()
    return summary


def render_agent_signature(sender: Sender, tenant: Tenant, settings: Settings) -> tuple[str, str]:
    """The agent's signature as HTML and text (for the console preview)."""
    branding = load_branding(tenant.branding)

    def link(asset_id: uuid.UUID) -> str:
        return asset_url(settings, tenant.id, asset_id)

    return (
        str(build_signature_html(sender, branding, tenant.name, link)),
        build_signature_text(sender, branding, tenant.name),
    )
