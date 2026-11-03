"""Company API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from lanereach.core.schemas import ResponseModel, StrictRequestModel


class BranchCreateRequest(StrictRequestModel):
    """A company branch."""

    name: str = Field(min_length=1, max_length=200, examples=["Dubai branch"])
    country: str = Field(pattern=r"^[A-Z]{2}$", examples=["AE"])
    city: str | None = Field(default=None, max_length=100)


class CompanyCreateRequest(StrictRequestModel):
    """A new prospect company."""

    name: str = Field(min_length=1, max_length=200, examples=["Gulf Importers LLC"])
    domain: str | None = Field(default=None, max_length=253, examples=["gulf-importers.example"])
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    industry: str | None = Field(default=None, max_length=100)
    branches: list[BranchCreateRequest] = Field(default_factory=list, max_length=50)


class CompanyUpdateRequest(StrictRequestModel):
    """Changes to a company. `frozen` freezes or unfreezes outreach to all its contacts."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    domain: str | None = Field(default=None, max_length=253)
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    industry: str | None = Field(default=None, max_length=100)
    frozen: bool | None = None
    frozen_reason: str | None = Field(default=None, max_length=200)


class BranchResponse(ResponseModel):
    """A company branch."""

    id: uuid.UUID
    name: str
    country: str
    city: str | None


class CompanyResponse(ResponseModel):
    """A company with its branches."""

    id: uuid.UUID
    name: str
    domain: str | None
    country: str | None
    industry: str | None
    frozen_at: datetime | None
    frozen_reason: str | None
    branches: list[BranchResponse] = Field(default_factory=list)
    contact_count: int = 0
    created_at: datetime
    updated_at: datetime
