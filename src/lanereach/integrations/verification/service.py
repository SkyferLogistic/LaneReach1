"""Verifying contacts' addresses with the tenant's verifier (mock until one is connected)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.core.clock import Clock
from lanereach.core.secrets import SecretBox
from lanereach.domain.contacts.models import Contact
from lanereach.integrations.outbound.models import IntegrationConnection, IntegrationKind
from lanereach.integrations.verification.verifier import (
    EmailStatus,
    EmailVerifier,
    MillionVerifierClient,
    MockEmailVerifier,
    VerificationResult,
)

MAX_BATCH = 500


@dataclass(slots=True)
class VerificationSummary:
    """What a batch found."""

    verifier: str
    checked: int = 0
    by_status: dict[str, int] = field(default_factory=dict)


async def build_email_verifier(
    session: AsyncSession, http: httpx.AsyncClient, box: SecretBox
) -> EmailVerifier:
    """The tenant's verifier: its MillionVerifier connection, else the offline rules.

    Args:
        session: Tenant session.
        http: Outbound HTTP client.
        box: Secret box (the API key is stored encrypted).

    Returns:
        The verifier.
    """
    connection = await session.scalar(
        select(IntegrationConnection).where(
            IntegrationConnection.kind == IntegrationKind.MILLIONVERIFIER,
            IntegrationConnection.is_active.is_(True),
        )
    )
    if connection is not None and connection.secret_encrypted:
        return MillionVerifierClient(http, box.decrypt(connection.secret_encrypted))
    return MockEmailVerifier()


async def verify_contact_emails(
    clock: Clock, verifier: EmailVerifier, contacts: Sequence[Contact]
) -> tuple[VerificationSummary, dict[str, VerificationResult]]:
    """Verify and store the status of each contact's address.

    An `unknown` answer (service down, quota) leaves a previous verdict in place.

    Args:
        clock: Time source.
        verifier: The verifier.
        contacts: Contacts to verify (at most `MAX_BATCH`).

    Returns:
        The summary and each contact's result by id.
    """
    now = clock.now()
    counts: Counter[str] = Counter()
    results: dict[str, VerificationResult] = {}
    for contact in contacts[:MAX_BATCH]:
        result = await verifier.verify(contact.email)
        results[str(contact.id)] = result
        counts[result.status.value] += 1
        if result.status is EmailStatus.UNKNOWN and contact.email_status != EmailStatus.UNKNOWN:
            continue
        contact.email_status = result.status.value
        contact.email_verified_at = now
        contact.updated_at = now
    return (
        VerificationSummary(verifier.name, sum(counts.values()), dict(counts)),
        results,
    )
