"""The authenticated caller, as seen by services."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from lanereach.auth.roles import Role, Scope


class AuthMethod(StrEnum):
    """How the principal authenticated."""

    OIDC = "oidc"
    DEV = "dev"
    API_KEY = "api_key"


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated caller acting within exactly one tenant.

    Attributes:
        subject: Stable identifier of the caller (IdP `sub`, dev subject, or `api_key:<id>`).
        tenant_id: The LaneReach tenant the caller acts in.
        roles: Human roles (empty for API keys).
        scopes: Effective permissions checked by endpoints.
        auth_method: How the caller authenticated.
    """

    subject: str
    tenant_id: uuid.UUID
    roles: frozenset[Role]
    scopes: frozenset[Scope]
    auth_method: AuthMethod

    def has_scope(self, scope: Scope) -> bool:
        """Return whether the principal holds `scope`.

        Args:
            scope: The scope to check.

        Returns:
            True when the scope is granted.
        """
        return scope in self.scopes
