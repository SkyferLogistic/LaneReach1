"""Email address verification: mock rules (default) or MillionVerifier with an API key.

Verification lowers bounces, which protects the agents' mailboxes (see mailbox health). An
`invalid` address is blocked by the `email_verification` guardrail; `risky` (role accounts,
disposable domains) and `catch_all` domains are sent to but shown to people.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import httpx

MILLIONVERIFIER_URL = "https://api.millionverifier.com/api/v3/"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.IGNORECASE)
_ROLE_ACCOUNTS = frozenset(
    {"info", "sales", "admin", "office", "contact", "hello", "support", "noreply", "no-reply"}
)
_DISPOSABLE = frozenset({"mailinator.com", "guerrillamail.com", "10minutemail.com", "yopmail.com"})


class EmailStatus(StrEnum):
    """What is known about an address."""

    UNKNOWN = "unknown"
    VALID = "valid"
    RISKY = "risky"
    CATCH_ALL = "catch_all"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """The verdict on one address."""

    status: EmailStatus
    reason: str


class EmailVerifier(Protocol):
    """Checks whether an address can receive email."""

    name: str

    async def verify(self, email: str) -> VerificationResult:
        """Verify one address."""
        ...


class MockEmailVerifier:
    """Offline rules: syntax, reserved and disposable domains, role accounts.

    Used until a verification service is connected, and in tests. Domains ending in
    `.invalid` (RFC 2606) are undeliverable and `catchall.` domains accept everything.
    """

    name = "mock"

    async def verify(self, email: str) -> VerificationResult:
        """Verify with local rules."""
        address = email.strip().lower()
        if not _EMAIL.match(address):
            return VerificationResult(EmailStatus.INVALID, "not a valid address")
        local, domain = address.rsplit("@", 1)
        if domain.endswith(".invalid") or domain.startswith("bounce."):
            return VerificationResult(EmailStatus.INVALID, "the domain does not accept email")
        if domain in _DISPOSABLE:
            return VerificationResult(EmailStatus.RISKY, "disposable mailbox")
        if domain.startswith("catchall."):
            return VerificationResult(EmailStatus.CATCH_ALL, "the domain accepts every address")
        if local in _ROLE_ACCOUNTS:
            return VerificationResult(EmailStatus.RISKY, "shared role mailbox")
        return VerificationResult(EmailStatus.VALID, "looks deliverable")


class MillionVerifierClient:
    """MillionVerifier single-address API.

    Args:
        http: Outbound HTTP client.
        api_key: The account's API key (stored encrypted, never logged).
    """

    name = "millionverifier"

    def __init__(self, http: httpx.AsyncClient, api_key: str) -> None:
        self._http = http
        self._api_key = api_key

    async def verify(self, email: str) -> VerificationResult:
        """Ask MillionVerifier; network or quota problems return `unknown`."""
        try:
            response = await self._http.get(
                MILLIONVERIFIER_URL,
                params={"api": self._api_key, "email": email, "timeout": 10},
                timeout=20,
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError):
            return VerificationResult(
                EmailStatus.UNKNOWN, "the verification service did not answer"
            )
        result = str(data.get("result", "")).lower()
        mapping = {
            "ok": EmailStatus.VALID,
            "catch_all": EmailStatus.CATCH_ALL,
            "disposable": EmailStatus.RISKY,
            "invalid": EmailStatus.INVALID,
            "unknown": EmailStatus.UNKNOWN,
        }
        status = mapping.get(result, EmailStatus.UNKNOWN)
        if status is EmailStatus.VALID and data.get("role"):
            status = EmailStatus.RISKY
        return VerificationResult(status, str(data.get("subresult") or result or "unknown")[:100])
