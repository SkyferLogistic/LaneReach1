"""Office and sender API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import EmailStr, Field, field_validator, model_validator

from lanereach.core.schemas import ResponseModel, StrictRequestModel
from lanereach.domain.senders.models import ServiceLine, SignatureMode


def _check_timezone(value: str | None) -> str | None:
    if value is None:
        return value
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone '{value}'") from exc
    return value


class OfficeCreateRequest(StrictRequestModel):
    """A new office."""

    name: str = Field(min_length=1, max_length=200, examples=["UAE"])
    country: str = Field(pattern=r"^[A-Z]{2}$", examples=["AE"])
    timezone: str = Field(examples=["Asia/Dubai"])
    default_language: str = Field(default="en", pattern=r"^[a-z]{2}$")
    owner_name: str = Field(min_length=1, max_length=200)
    owner_email: EmailStr
    signature: str = Field(default="", max_length=2000)

    _timezone = field_validator("timezone")(_check_timezone)


class OfficeUpdateRequest(StrictRequestModel):
    """Changes to an office. `sending_paused` is the office kill switch."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    timezone: str | None = None
    default_language: str | None = Field(default=None, pattern=r"^[a-z]{2}$")
    owner_name: str | None = Field(default=None, min_length=1, max_length=200)
    owner_email: EmailStr | None = None
    signature: str | None = Field(default=None, max_length=2000)
    sending_paused: bool | None = Field(
        default=None, description="Pause (true) or resume (false) all sending from this office."
    )

    _timezone = field_validator("timezone")(_check_timezone)


class OfficeResponse(ResponseModel):
    """An office."""

    id: uuid.UUID
    name: str
    country: str
    timezone: str
    default_language: str
    owner_name: str
    owner_email: str
    signature: str
    signature_asset_id: uuid.UUID | None
    sending_paused_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SenderCreateRequest(StrictRequestModel):
    """A new sender mailbox."""

    office_id: uuid.UUID
    display_name: str = Field(min_length=1, max_length=200, examples=["Maya Rahman"])
    email: EmailStr
    daily_cap: int = Field(default=50, ge=1, le=2000)
    is_active: bool = True
    service_line: ServiceLine = Field(default=ServiceLine.ANY, description="Air or ocean agent.")
    job_title: str | None = Field(default=None, max_length=200, examples=["Marketing Executive"])
    phone: str | None = Field(default=None, max_length=50, examples=["+1 905-677-5000"])
    mobile: str | None = Field(
        default=None, max_length=50, description="WhatsApp number.", examples=["+1 647-295-8454"]
    )
    signature_mode: SignatureMode = SignatureMode.GENERATED
    signature_html: str | None = Field(default=None, max_length=20_000)
    signature_asset_id: uuid.UUID | None = None


class SenderUpdateRequest(StrictRequestModel):
    """Changes to a sender. `clear_flag` re-enables a mailbox flagged after a block."""

    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    daily_cap: int | None = Field(default=None, ge=1, le=2000)
    is_active: bool | None = None
    clear_flag: bool = False
    service_line: ServiceLine | None = None
    job_title: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=50)
    mobile: str | None = Field(default=None, max_length=50)
    signature_mode: SignatureMode | None = None
    signature_html: str | None = Field(default=None, max_length=20_000)
    signature_asset_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _require_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("nothing to update")
        return self


class SenderResponse(ResponseModel):
    """A sender mailbox."""

    id: uuid.UUID
    office_id: uuid.UUID
    display_name: str
    email: str
    daily_cap: int
    is_active: bool
    flagged_at: datetime | None
    flag_reason: str | None
    service_line: ServiceLine
    job_title: str | None
    phone: str | None
    mobile: str | None
    signature_mode: SignatureMode
    signature_html: str | None
    signature_asset_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    mailbox_status: str | None = Field(
        default=None,
        description="`verified`, `unverified` or `error` when a mailbox is connected.",
    )
