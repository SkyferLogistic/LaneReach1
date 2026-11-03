"""Base classes for API request and response schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StrictRequestModel(BaseModel):
    """Base for request bodies: unknown fields are rejected (`extra="forbid"`)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ResponseModel(BaseModel):
    """Base for response bodies, readable from ORM objects."""

    model_config = ConfigDict(from_attributes=True)
