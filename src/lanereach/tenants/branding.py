"""Tenant branding used to generate agent signatures (stored as JSON on the tenant)."""

from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class TenantBranding(BaseModel):
    """The company look of every generated signature.

    Mirrors the signature block of the sample script: agent name and title on the left with
    the offices strip, the logo and tagline in the middle, contact details on the right and a
    row of association badges underneath.
    """

    model_config = ConfigDict(extra="forbid")

    company_name: str | None = Field(default=None, max_length=200)
    tagline: str | None = Field(default=None, max_length=200, examples=["Beyond Your Destination"])
    offices_strip: str | None = Field(
        default=None,
        max_length=300,
        examples=["Canada | USA | France | Spain | UAE | India | Sri Lanka"],
    )
    address: str | None = Field(default=None, max_length=300)
    phone: str | None = Field(default=None, max_length=50, description="Default phone for agents.")
    fax: str | None = Field(default=None, max_length=50)
    website: str | None = Field(default=None, max_length=300)
    primary_color: str = Field(default="#1f3f8f", pattern=r"^#[0-9a-fA-F]{6}$")
    accent_color: str = Field(default="#6cbf4a", pattern=r"^#[0-9a-fA-F]{6}$")
    logo_asset_id: uuid.UUID | None = None
    badge_asset_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)
    qr_asset_id: uuid.UUID | None = Field(
        default=None, description="QR code under the agent's name (for example a vCard)."
    )
    promo_asset_id: uuid.UUID | None = Field(
        default=None, description="Banner across the bottom of the signature (an offer)."
    )
    promo_link: str | None = Field(default=None, max_length=500, description="Where it links.")
    lane_origin_countries: list[Annotated[str, Field(pattern=r"^[A-Z]{2}$")]] = Field(
        default_factory=list,
        max_length=10,
        description="Where the outreach lanes start (ISO codes, first = map origin). These "
        "countries are left out of the destinations on the lane map.",
        examples=[["CA", "US"]],
    )


def load_branding(raw: dict[str, object] | None) -> TenantBranding:
    """Branding from the tenant's JSON (unknown keys ignored, invalid values reset).

    Args:
        raw: `tenants.branding`.

    Returns:
        The branding.
    """
    data = {key: value for key, value in (raw or {}).items() if key in TenantBranding.model_fields}
    try:
        return TenantBranding.model_validate(data)
    except ValueError:
        return TenantBranding()
