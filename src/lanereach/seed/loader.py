"""Load demo tenants (`make seed`) from `data/demo_tenants.json`.

The dataset is data, not code: tenant names, offices, people and companies live in the JSON
file. The loader creates each tenant with its offices, senders, companies (with branches and
contacts), templates and published workflows. Workflow graphs reference offices as
`@office:<key>`; the loader swaps in the created office IDs.

Seeding runs as the owner role (like migrations) because it creates tenants. It is idempotent
per tenant: a tenant whose slug already exists is left untouched, so re-running never
duplicates or overwrites anything a person changed.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lanereach.auth.principal import AuthMethod, Principal
from lanereach.auth.roles import Role, scopes_for_roles
from lanereach.automation.nodes.registry import NodeRegistry
from lanereach.automation.workflows.graph import WorkflowGraph
from lanereach.automation.workflows.service import WorkflowService
from lanereach.core.clock import Clock
from lanereach.core.config import Settings
from lanereach.core.database import create_engine, create_session_factory
from lanereach.core.tenancy import open_tenant_session
from lanereach.domain.companies.models import Company, CompanyBranch
from lanereach.domain.contacts.models import Contact
from lanereach.domain.countries.models import Country
from lanereach.domain.crm.models import Deal
from lanereach.domain.crm.service import ensure_default_pipeline, open_deal_for_lead
from lanereach.domain.leads.models import Lead
from lanereach.domain.offices.models import Office
from lanereach.domain.scripts.service import ScriptLibraryService
from lanereach.domain.senders.models import Sender, ServiceLine
from lanereach.domain.templates.models import Template, TemplateSource
from lanereach.integrations.documents.store import LocalDocumentStore
from lanereach.tenants.branding import TenantBranding
from lanereach.tenants.models import Tenant, TenantStatus

logger = structlog.get_logger(__name__)

DATASET_PATH = Path(__file__).with_name("data") / "demo_tenants.json"
OFFICE_REFERENCE = "@office:"
SEED_SUBJECT = "seed"


@dataclass
class SeededTenant:
    """What was created for one tenant (or why it was skipped)."""

    name: str
    slug: str
    tenant_id: uuid.UUID
    created: bool
    counts: dict[str, int] = field(default_factory=dict)


def load_seed_dataset(path: Path = DATASET_PATH) -> dict[str, Any]:
    """Read the seed dataset.

    Args:
        path: JSON file.

    Returns:
        The parsed dataset.
    """
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _resolve_offices(value: Any, offices: dict[str, uuid.UUID]) -> Any:
    """Replace `@office:<key>` strings anywhere in a graph with office IDs."""
    if isinstance(value, dict):
        return {key: _resolve_offices(item, offices) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_offices(item, offices) for item in value]
    if isinstance(value, str) and value.startswith(OFFICE_REFERENCE):
        return str(offices[value.removeprefix(OFFICE_REFERENCE)])
    return value


async def _seed_tenant_rows(
    session: AsyncSession,
    clock: Clock,
    registry: NodeRegistry,
    settings: Settings,
    tenant_id: uuid.UUID,
    spec: dict[str, Any],
) -> dict[str, int]:
    now = clock.now()
    offices: dict[str, uuid.UUID] = {}
    for office_spec in spec["offices"]:
        office = Office(
            tenant_id=tenant_id,
            name=office_spec["name"],
            country=office_spec["country"],
            timezone=office_spec["timezone"],
            default_language=office_spec["default_language"],
            owner_name=office_spec["owner_name"],
            owner_email=office_spec["owner_email"],
            signature=office_spec.get("signature", ""),
            created_at=now,
            updated_at=now,
        )
        session.add(office)
        await session.flush()
        offices[office_spec["key"]] = office.id
    for sender_spec in spec["senders"]:
        session.add(
            Sender(
                tenant_id=tenant_id,
                office_id=offices[sender_spec["office"]],
                display_name=sender_spec["display_name"],
                email=sender_spec["email"],
                daily_cap=sender_spec.get("daily_cap", 50),
                service_line=ServiceLine(sender_spec.get("service_line", "any")),
                job_title=sender_spec.get("job_title"),
                phone=sender_spec.get("phone"),
                created_at=now,
                updated_at=now,
            )
        )
    contacts = 0
    for company_spec in spec["companies"]:
        company = Company(
            tenant_id=tenant_id,
            name=company_spec["name"],
            domain=company_spec["domain"],
            country=company_spec["country"],
            industry=company_spec.get("industry"),
            created_at=now,
            updated_at=now,
        )
        session.add(company)
        await session.flush()
        for branch_spec in company_spec.get("branches", []):
            session.add(
                CompanyBranch(
                    tenant_id=tenant_id,
                    company_id=company.id,
                    name=branch_spec["name"],
                    country=branch_spec["country"],
                    city=branch_spec.get("city"),
                    created_at=now,
                )
            )
        for contact_spec in company_spec["contacts"]:
            session.add(
                Contact(
                    tenant_id=tenant_id,
                    company_id=company.id,
                    office_id=offices[company_spec["office"]],
                    email=contact_spec["email"],
                    first_name=contact_spec.get("first_name"),
                    last_name=contact_spec.get("last_name"),
                    job_title=contact_spec.get("job_title"),
                    country=company_spec["country"],
                    city=contact_spec.get("city"),
                    language=contact_spec.get("language"),
                    network=contact_spec.get("network"),
                    tags=[],
                    created_at=now,
                    updated_at=now,
                )
            )
            contacts += 1
    templates = 0
    for key, languages in spec["templates"].items():
        for language, (subject, body) in languages.items():
            session.add(
                Template(
                    tenant_id=tenant_id,
                    key=key,
                    language=language,
                    name=f"{key} ({language})",
                    subject=subject,
                    body=body,
                    created_at=now,
                    updated_at=now,
                )
            )
            templates += 1
    for country_spec in spec.get("countries", []):
        session.add(
            Country(
                tenant_id=tenant_id,
                country=country_spec["country"],
                name=country_spec["name"],
                language=country_spec["language"],
                to_country_phrase=country_spec["to_country_phrase"],
                allow_language_fallback=country_spec.get("allow_language_fallback", True),
                is_enabled=True,
                created_at=now,
                updated_at=now,
            )
        )
    tenant = await session.get(Tenant, tenant_id)
    assert tenant is not None  # noqa: S101 - created by the caller
    if "branding" in spec:
        tenant.branding = TenantBranding.model_validate(spec["branding"]).model_dump(mode="json")
    await session.flush()

    scripts = 0
    if spec.get("sample_scripts"):
        # Written into the tenant's library folder exactly as marketing would upload them.
        library = ScriptLibraryService(
            session,
            clock,
            settings,
            LocalDocumentStore(Path(settings.local_documents_path) / str(tenant_id)),
            tenant,
        )
        for script in spec["sample_scripts"]:
            await library.create_blank_script(
                script_name=script["name"],
                service_line=ServiceLine(script["service_line"]),
                language=script["language"],
                subject=script["subject"],
                body=script["body"],
            )
            scripts += 1

    roles = frozenset({Role.ADMIN})
    principal = Principal(SEED_SUBJECT, tenant_id, roles, scopes_for_roles(roles), AuthMethod.DEV)
    service = WorkflowService(
        session, clock, registry, max_cold_emails=settings.max_cold_emails_per_sequence
    )
    for workflow_spec in spec["workflows"]:
        created = await service.create_workflow(
            principal,
            name=workflow_spec["name"],
            description=workflow_spec.get("description", ""),
            graph=WorkflowGraph.model_validate(_resolve_offices(workflow_spec["graph"], offices)),
        )
        await service.publish_workflow(created.workflow.id, None)
    return {
        "offices": len(offices),
        "senders": len(spec["senders"]),
        "companies": len(spec["companies"]),
        "contacts": contacts,
        "templates": templates,
        "countries": len(spec.get("countries", [])),
        "scripts": scripts,
        "workflows": len(spec["workflows"]),
    }


async def _top_up_existing_tenant(
    session: AsyncSession,
    clock: Clock,
    settings: Settings,
    tenant_id: uuid.UUID,
    spec: dict[str, Any],
) -> dict[str, int]:
    """Add dataset parts introduced after the tenant was first seeded; never overwrite.

    Countries are added when the tenant has none, branding when it is empty, agents' service
    lines and titles only where still unset, and sample scripts when the library has none.

    Returns:
        What was added (empty when nothing was missing).
    """
    added: dict[str, int] = {}
    now = clock.now()
    tenant = await session.get(Tenant, tenant_id)
    assert tenant is not None  # noqa: S101 - found by slug
    if spec.get("countries") and await session.scalar(select(Country.id).limit(1)) is None:
        for country_spec in spec["countries"]:
            session.add(
                Country(
                    tenant_id=tenant_id,
                    country=country_spec["country"],
                    name=country_spec["name"],
                    language=country_spec["language"],
                    to_country_phrase=country_spec["to_country_phrase"],
                    allow_language_fallback=country_spec.get("allow_language_fallback", True),
                    is_enabled=True,
                    created_at=now,
                    updated_at=now,
                )
            )
        added["countries"] = len(spec["countries"])
    if spec.get("branding") and not tenant.branding:
        tenant.branding = TenantBranding.model_validate(spec["branding"]).model_dump(mode="json")
        added["branding"] = 1
    by_email = {sender["email"]: sender for sender in spec.get("senders", [])}
    for agent in (await session.scalars(select(Sender))).all():
        sender_spec = by_email.get(agent.email)
        if (
            sender_spec
            and agent.service_line is ServiceLine.ANY
            and sender_spec.get("service_line")
        ):
            agent.service_line = ServiceLine(sender_spec["service_line"])
            agent.job_title = agent.job_title or sender_spec.get("job_title")
            added["agents"] = added.get("agents", 0) + 1
    await session.flush()
    library_scripts = await session.scalar(
        select(Template.id).where(Template.source == TemplateSource.SHAREPOINT).limit(1)
    )
    if spec.get("sample_scripts") and library_scripts is None:
        library = ScriptLibraryService(
            session,
            clock,
            settings,
            LocalDocumentStore(Path(settings.local_documents_path) / str(tenant_id)),
            tenant,
        )
        for script in spec["sample_scripts"]:
            await library.create_blank_script(
                script_name=script["name"],
                service_line=ServiceLine(script["service_line"]),
                language=script["language"],
                subject=script["subject"],
                body=script["body"],
            )
        added["scripts"] = len(spec["sample_scripts"])
    deals = await _open_crm(session, clock)
    if deals:
        added["deals"] = deals
    return added


async def _open_crm(session: AsyncSession, clock: Clock) -> int:
    """The default pipeline, and a deal for every lead that has none (leads before the CRM).

    Returns:
        How many deals were opened.
    """
    await ensure_default_pipeline(session, clock)
    leads = (
        await session.scalars(
            select(Lead).where(~select(Deal.id).where(Deal.lead_id == Lead.id).exists())
        )
    ).all()
    for lead in leads:
        await open_deal_for_lead(session, clock, lead, actor="seed")
    return len(leads)


async def seed_demo_tenants(
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
    registry: NodeRegistry,
    settings: Settings,
    dataset: dict[str, Any] | None = None,
) -> list[SeededTenant]:
    """Create every dataset tenant that does not exist yet.

    Args:
        session_factory: Sessions as the **owner** role (tenants are created here).
        clock: Time source.
        registry: Node registry (workflows are validated and published).
        settings: Configuration (cold-email limit).
        dataset: Parsed dataset; defaults to the bundled file.

    Returns:
        One entry per dataset tenant.
    """
    results: list[SeededTenant] = []
    for spec in (dataset or load_seed_dataset())["tenants"]:
        async with session_factory() as session:
            existing = await session.scalar(select(Tenant).where(Tenant.slug == spec["slug"]))
        if existing is not None:
            async with open_tenant_session(session_factory, existing.id) as session:
                added = await _top_up_existing_tenant(session, clock, settings, existing.id, spec)
            results.append(
                SeededTenant(spec["name"], spec["slug"], existing.id, created=False, counts=added)
            )
            logger.info("seed_tenant_exists", tenant_id=str(existing.id), **added)
            continue
        tenant_id = uuid.uuid4()
        async with open_tenant_session(session_factory, tenant_id) as session:
            now = clock.now()
            session.add(
                Tenant(
                    id=tenant_id,
                    name=spec["name"],
                    slug=spec["slug"],
                    status=TenantStatus.ACTIVE,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            counts = await _seed_tenant_rows(session, clock, registry, settings, tenant_id, spec)
            await _open_crm(session, clock)
        results.append(SeededTenant(spec["name"], spec["slug"], tenant_id, True, counts))
        logger.info("seed_tenant_created", tenant_id=str(tenant_id), **counts)
    return results


async def seed_from_settings(settings: Settings, clock: Clock) -> list[SeededTenant]:
    """`lanereach-admin seed`: seed using the owner database URL.

    Args:
        settings: Configuration (owner URL).
        clock: Time source.

    Returns:
        What was seeded.
    """
    from lanereach.automation.nodes.registry import load_node_catalog

    engine = create_engine(settings.database_owner_url.get_secret_value(), pool_size=2)
    try:
        return await seed_demo_tenants(
            create_session_factory(engine), clock, load_node_catalog(), settings
        )
    finally:
        await engine.dispose()
