"""Roles, scopes and the default scope set granted to each role.

Endpoints check *scopes*, never roles. Roles are a convenience for humans (assigned in the IdP
or in dev tokens) that expand to scope sets here; API keys carry scopes directly.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class Role(StrEnum):
    """A human user's role within a tenant."""

    ADMIN = "admin"
    CAMPAIGN_MANAGER = "campaign_manager"
    OFFICE_OWNER = "office_owner"
    PRICING = "pricing"
    REVIEWER = "reviewer"
    VIEWER = "viewer"


class Scope(StrEnum):
    """A permission checked by an endpoint."""

    AI_RUN = "ai:run"
    AI_REVIEW = "ai:review"
    WORKFLOWS_READ = "workflows:read"
    WORKFLOWS_WRITE = "workflows:write"
    WORKFLOWS_PUBLISH = "workflows:publish"
    ENROLLMENTS_WRITE = "enrollments:write"
    EVENTS_WRITE = "events:write"
    CRM_WRITE = "crm:write"
    ADMIN_MANAGE = "admin:manage"


ROLE_SCOPES: dict[Role, frozenset[Scope]] = {
    Role.ADMIN: frozenset(Scope),
    Role.CAMPAIGN_MANAGER: frozenset(
        {
            Scope.AI_RUN,
            Scope.WORKFLOWS_READ,
            Scope.WORKFLOWS_WRITE,
            Scope.WORKFLOWS_PUBLISH,
            Scope.ENROLLMENTS_WRITE,
            Scope.EVENTS_WRITE,
            Scope.CRM_WRITE,
        }
    ),
    # Office owners handle handovers from their office: they review drafts and enroll contacts.
    # They also work their deals, tasks and notes in the CRM.
    Role.OFFICE_OWNER: frozenset(
        {
            Scope.AI_RUN,
            Scope.AI_REVIEW,
            Scope.WORKFLOWS_READ,
            Scope.ENROLLMENTS_WRITE,
            Scope.CRM_WRITE,
        }
    ),
    # Pricing works quote-ready leads and the deal pipeline: extraction, review, CRM.
    Role.PRICING: frozenset({Scope.AI_RUN, Scope.AI_REVIEW, Scope.WORKFLOWS_READ, Scope.CRM_WRITE}),
    Role.REVIEWER: frozenset({Scope.AI_REVIEW, Scope.WORKFLOWS_READ}),
    Role.VIEWER: frozenset({Scope.WORKFLOWS_READ}),
}


def parse_roles(values: Iterable[str]) -> frozenset[Role]:
    """Convert role names from a token into `Role` values, ignoring unknown names.

    Unknown names are dropped rather than rejected because IdPs often put unrelated roles in
    the same claim.

    Args:
        values: Raw role names.

    Returns:
        The recognised roles.
    """
    known = {role.value for role in Role}
    return frozenset(Role(value) for value in values if value in known)


def parse_scopes(values: Iterable[str]) -> frozenset[Scope]:
    """Convert scope names into `Scope` values, ignoring unknown names.

    Args:
        values: Raw scope names.

    Returns:
        The recognised scopes.
    """
    known = {scope.value for scope in Scope}
    return frozenset(Scope(value) for value in values if value in known)


def scopes_for_roles(roles: Iterable[Role]) -> frozenset[Scope]:
    """Return the union of the default scopes granted to `roles`.

    Args:
        roles: The principal's roles.

    Returns:
        Every scope any of the roles grants.
    """
    granted: set[Scope] = set()
    for role in roles:
        granted |= ROLE_SCOPES[role]
    return frozenset(granted)
