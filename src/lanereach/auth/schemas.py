"""Request and response schemas for the auth endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import ConfigDict, Field

from lanereach.auth.models import ApiKeyEnvironment
from lanereach.auth.principal import AuthMethod
from lanereach.auth.roles import Role, Scope
from lanereach.core.schemas import ResponseModel, StrictRequestModel


class DevTokenRequest(StrictRequestModel):
    """Parameters of a dev token."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "tenant_id": "0b7c9a52-4c1e-4a55-9a53-3f5d2f1e8a10",
                    "subject": "maya@forwarder.example",
                    "roles": ["campaign_manager"],
                    "expires_in_seconds": 3600,
                }
            ]
        }
    )

    tenant_id: uuid.UUID
    subject: str = Field(default="dev-user", min_length=1, max_length=200)
    roles: list[Role] = Field(default_factory=lambda: [Role.ADMIN])
    scopes: list[Scope] | None = Field(
        default=None, description="Explicit scopes. When omitted, scopes come from the roles."
    )
    expires_in_seconds: int = Field(default=3600, ge=60, le=86_400)


class DevTokenResponse(ResponseModel):
    """A dev bearer token."""

    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth token type, not a secret
    expires_at: datetime


class PrincipalResponse(ResponseModel):
    """Who the caller is, as resolved from their credential."""

    subject: str
    tenant_id: uuid.UUID
    tenant_name: str | None = None
    roles: list[Role]
    scopes: list[Scope]
    auth_method: AuthMethod


class ApiKeyCreateRequest(StrictRequestModel):
    """Parameters of a new API key."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"name": "CRM sync", "scopes": ["events:write", "workflows:read"]}]
        }
    )

    name: str = Field(min_length=1, max_length=200)
    scopes: list[Scope] = Field(min_length=1)
    expires_at: datetime | None = None


class ApiKeyResponse(ResponseModel):
    """An API key's metadata. The secret itself is never returned after creation."""

    id: uuid.UUID
    name: str
    key_prefix: str
    environment: ApiKeyEnvironment
    scopes: list[str]
    created_by: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreatedResponse(ApiKeyResponse):
    """A new API key including its secret, which is shown only in this response."""

    secret: str | None = Field(
        description="The full API key. Shown once; null when an idempotent retry replays the "
        "original response (the secret is never stored)."
    )
