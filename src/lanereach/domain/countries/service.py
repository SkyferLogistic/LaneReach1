"""Destination-country table: CRUD and the language decision for a contact."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.core.clock import Clock
from lanereach.core.errors import NotFoundError
from lanereach.core.languages import split_language_variant
from lanereach.core.tenancy import require_session_tenant_id
from lanereach.domain.contacts.models import Contact
from lanereach.domain.countries.models import Country
from lanereach.domain.offices.models import Office


@dataclass(frozen=True, slots=True)
class LanguageChoice:
    """Which template languages to try for a contact, in order.

    `strict` means only the first language may be used: when it has no active script the email
    is held (the country's wording must not be replaced by another language's).
    """

    languages: list[str]
    strict: bool
    country: Country | None


async def find_country(session: AsyncSession, code: str | None) -> Country | None:
    """The tenant's row for an ISO country code (None when not in the table)."""
    if not code:
        return None
    return await session.scalar(select(Country).where(Country.country == code.upper()))


def choose_languages(contact: Contact, office: Office, country: Country | None) -> LanguageChoice:
    """Template languages to try for a contact.

    The destination-country table decides first (Turkey -> `tr`, Brazil -> `pt-br`); then the
    variant's base language, the contact's language, the office's default and English, unless
    the country forbids falling back (USA/Canada need the North America lane wording).

    Args:
        contact: Recipient.
        office: Sending office.
        country: The contact's row in the countries table, if any.

    Returns:
        The choice.
    """
    if country is not None and country.is_enabled:
        if not country.allow_language_fallback:
            return LanguageChoice([country.language], strict=True, country=country)
        base, _ = split_language_variant(country.language)
        ordered = [country.language, base, contact.language, office.default_language, "en"]
    else:
        ordered = [contact.language, office.default_language, "en"]
    languages = list(dict.fromkeys(code for code in ordered if code))
    return LanguageChoice(languages, strict=False, country=country)


# English country names that read "to the ..." ("to the Netherlands", "to the UAE").
_NAMES_WITH_THE = frozenset({"netherlands", "philippines", "bahamas", "maldives", "czech republic"})


def destination_phrase(country: Country, template_language: str) -> str:
    """The `{to_country}` wording for a script in `template_language`.

    The country's phrase is written in the country's language (`Türkiye'ye`), so it is only
    used when the script is in that language (any variant of it). A fallback English script
    gets "to <country>"; any other fallback language gets the plain country name, because a
    Turkish phrase inside a Spanish email would read worse than the name alone.

    Args:
        country: The destination-country row.
        template_language: Language of the script being sent.

    Returns:
        The phrase to insert.
    """
    script_base, _ = split_language_variant(template_language)
    country_base, _ = split_language_variant(country.language)
    if script_base == country_base:
        return country.to_country_phrase
    if script_base == "en":
        name = country.name
        if name.lower() in _NAMES_WITH_THE or name.lower().startswith("united "):
            return f"to the {name}"
        return f"to {name}"
    return country.name


class CountryService:
    """The countries table.

    Args:
        session: Tenant session.
        clock: Time source.
    """

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    async def list_countries(self) -> Sequence[Country]:
        """Every country, by language then name."""
        return (
            await self._session.scalars(select(Country).order_by(Country.language, Country.name))
        ).all()

    async def upsert_country(
        self,
        code: str,
        *,
        name: str,
        language: str,
        to_country_phrase: str,
        allow_language_fallback: bool,
        is_enabled: bool,
    ) -> Country:
        """Create or replace a country's row.

        Args:
            code: ISO 3166-1 alpha-2.
            name: Display name.
            language: Template language variant.
            to_country_phrase: Destination as written in that language (`vers la France`).
            allow_language_fallback: Whether another language may be used when none exists.
            is_enabled: Whether the row applies (disabled rows fall back to contact language).

        Returns:
            The row.
        """
        now = self._clock.now()
        row = await find_country(self._session, code)
        if row is None:
            row = Country(
                tenant_id=require_session_tenant_id(self._session),
                country=code.upper(),
                created_at=now,
            )
            self._session.add(row)
        row.name = name
        row.language = language
        row.to_country_phrase = to_country_phrase
        row.allow_language_fallback = allow_language_fallback
        row.is_enabled = is_enabled
        row.updated_at = now
        await self._session.flush()
        return row

    async def delete_country(self, code: str) -> None:
        """Remove a country's row.

        Raises:
            NotFoundError: If the country is not in the table.
        """
        row = await find_country(self._session, code)
        if row is None:
            raise NotFoundError("Country not found.")
        await self._session.delete(row)

    async def find_by_name(self, name: str) -> Country | None:
        """Row whose name matches (case-insensitive), for banners named `Turkey.png`."""
        return await self._session.scalar(
            select(Country).where(func.lower(Country.name) == name.strip().lower()).limit(1)
        )
