"""Bulk contact import from CSV: the forwarder's contact database in one upload.

Rows are matched by email (new contacts are created, existing ones get the non-empty values of
the row). Companies and offices are matched by name; a missing company is created. Tags from
the file, and an optional tag for every row, are added the normal way, so an import can start
workflows ("tag everyone `air-intro`").
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from email_validator import EmailNotValidError, validate_email
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.automation.engine.queue import TaskQueue
from lanereach.core.clock import Clock
from lanereach.core.csv_files import read_csv_rows
from lanereach.core.errors import BusinessRuleViolationError, ConflictError
from lanereach.core.languages import parse_language_variant, split_language_variant
from lanereach.domain.companies.models import Company
from lanereach.domain.companies.schemas import CompanyCreateRequest
from lanereach.domain.companies.service import CompanyService
from lanereach.domain.contacts.models import Contact
from lanereach.domain.contacts.schemas import ContactCreateRequest
from lanereach.domain.contacts.service import ContactService
from lanereach.domain.countries.models import Country
from lanereach.domain.offices.models import Office

MAX_CONTACT_ROWS = 5000
_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _:-]{0,49}$")
_UPDATABLE = ("first_name", "last_name", "job_title", "city", "network", "timezone")


@dataclass
class ContactImportSummary:
    """What an import did."""

    created: int = 0
    updated: int = 0
    tagged: int = 0
    companies_created: int = 0
    errors: list[str] = field(default_factory=list)


def split_tags(value: str) -> list[str]:
    """`air-intro; uae | vip` → `["air-intro", "uae", "vip"]`."""
    return [tag.strip() for tag in re.split(r"[;|]", value) if tag.strip()]


class ContactImporter:
    """Imports one CSV file into the tenant.

    Args:
        session: Tenant session.
        clock: Time source.
        task_queue: Where workflow events are enqueued after commit.
    """

    def __init__(self, session: AsyncSession, clock: Clock, task_queue: TaskQueue | None) -> None:
        self._session = session
        self._clock = clock
        self._contacts = ContactService(session, clock, task_queue)
        self._companies = CompanyService(session, clock)
        self._company_cache: dict[str, Company] = {}

    async def import_csv(self, content: bytes, *, tag: str | None = None) -> ContactImportSummary:
        """Import the file.

        Columns: `email` and optionally `first_name, last_name, job_title, company, country`
        (ISO code or a name from the countries table), `city, language` (code or name),
        `network, office` (name), `tags` (separated by `;` or `|`), `owner_email, timezone`.

        Args:
            content: CSV bytes.
            tag: A tag added to every imported contact (starts matching workflows).

        Returns:
            Counts and row errors (rows with errors are skipped, the rest imported).

        Raises:
            BusinessRuleViolationError: For an unreadable file, missing `email` column, too
                many rows or an invalid `tag`.
        """
        if tag is not None and not _TAG.fullmatch(tag):
            raise BusinessRuleViolationError(
                "The tag may use letters, digits, spaces, '-', '_' and ':' (at most 50)."
            )
        offices = {
            office.name.strip().lower(): office.id
            for office in (await self._session.scalars(select(Office))).all()
        }
        countries = {
            country.name.strip().lower(): country.country
            for country in (await self._session.scalars(select(Country))).all()
        }
        summary = ContactImportSummary()
        for number, row in read_csv_rows(content, ("email",), max_rows=MAX_CONTACT_ROWS):
            problem = await self._import_row(row, offices, countries, tag, summary)
            if problem:
                summary.errors.append(f"Row {number}: {problem}")
        return summary

    async def _import_row(
        self,
        row: dict[str, str],
        offices: dict[str, uuid.UUID],
        countries: dict[str, str],
        tag: str | None,
        summary: ContactImportSummary,
    ) -> str | None:
        try:
            email = validate_email(row["email"], check_deliverability=False).normalized.lower()
        except EmailNotValidError:
            return f"invalid email {row['email']!r}"
        country = self._country(row.get("country", ""), countries)
        if row.get("country") and country is None:
            return f"unknown country {row['country']!r} (use an ISO code like TR)"
        language = self._language(row.get("language", ""))
        if row.get("language") and language is None:
            return f"unknown language {row['language']!r}"
        office_id = offices.get(row.get("office", "").lower()) if row.get("office") else None
        if row.get("office") and office_id is None:
            return f"unknown office {row['office']!r}"
        tags = split_tags(row.get("tags", "")) + ([tag] if tag else [])
        bad_tags = [value for value in tags if not _TAG.fullmatch(value)]
        if bad_tags:
            return f"invalid tag {bad_tags[0]!r}"

        company_id = None
        if row.get("company"):
            company, created = await self._company(row["company"], country)
            company_id = company.id
            summary.companies_created += int(created)

        contact = await self._session.scalar(
            select(Contact).where(func.lower(Contact.email) == email)
        )
        if contact is None:
            try:
                request = ContactCreateRequest(
                    email=email,
                    first_name=row.get("first_name") or None,
                    last_name=row.get("last_name") or None,
                    job_title=row.get("job_title") or None,
                    country=country,
                    city=row.get("city") or None,
                    language=language,
                    timezone=row.get("timezone") or None,
                    company_id=company_id,
                    office_id=office_id,
                    owner_email=row.get("owner_email") or None,
                    network=row.get("network") or None,
                )
            except ValidationError as exc:
                return "; ".join(error["msg"] for error in exc.errors())
            try:
                async with self._session.begin_nested():
                    contact = await self._contacts.create_contact(request)
            except ConflictError as exc:
                return str(exc)
            summary.created += 1
        else:
            for name in _UPDATABLE:
                if row.get(name):
                    setattr(contact, name, row[name][:200])
            contact.country = country or contact.country
            contact.language = language or contact.language
            contact.company_id = company_id or contact.company_id
            contact.office_id = office_id or contact.office_id
            contact.updated_at = self._clock.now()
            summary.updated += 1
        for value in dict.fromkeys(tags):
            before = len(contact.tags)
            contact = await self._contacts.add_tag(contact.id, value)
            summary.tagged += int(len(contact.tags) > before)
        return None

    @staticmethod
    def _country(value: str, countries: dict[str, str]) -> str | None:
        if not value:
            return None
        if re.fullmatch(r"[A-Za-z]{2}", value):
            return value.upper()
        return countries.get(value.lower())

    @staticmethod
    def _language(value: str) -> str | None:
        if not value:
            return None
        variant = parse_language_variant(value)
        if variant is None:
            return value.lower() if re.fullmatch(r"[A-Za-z]{2}", value) else None
        # Contacts carry the base language; variants belong to scripts.
        return split_language_variant(variant)[0]

    async def _company(self, name: str, country: str | None) -> tuple[Company, bool]:
        key = name.strip().lower()
        cached = self._company_cache.get(key)
        if cached is not None:
            return cached, False
        company = await self._session.scalar(
            select(Company).where(func.lower(Company.name) == key).limit(1)
        )
        created = company is None
        if company is None:
            company = await self._companies.create_company(
                CompanyCreateRequest(name=name.strip()[:200], country=country)
            )
        self._company_cache[key] = company
        return company, created
