"""Contact and message API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import EmailStr, Field

from lanereach.core.schemas import ResponseModel, StrictRequestModel
from lanereach.domain.contacts.models import ContactStage
from lanereach.domain.messages.models import MessageDirection, MessageKind

TagField = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9][A-Za-z0-9 _:-]*$")


class ContactCreateRequest(StrictRequestModel):
    """A new contact. Creating it publishes `contact.created` (workflows may start)."""

    email: EmailStr
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    job_title: str | None = Field(default=None, max_length=200)
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$", examples=["AE"])
    city: str | None = Field(default=None, max_length=100)
    language: str | None = Field(default=None, pattern=r"^[a-z]{2}$", examples=["en"])
    timezone: str | None = Field(default=None, max_length=64)
    company_id: uuid.UUID | None = None
    branch_id: uuid.UUID | None = None
    office_id: uuid.UUID | None = None
    stage: ContactStage = ContactStage.NEW
    owner_email: EmailStr | None = None
    network: str | None = Field(default=None, max_length=50, examples=["WCA"])
    tags: list[str] = Field(default_factory=list, max_length=50)


class ContactUpdateRequest(StrictRequestModel):
    """Changes to a contact (tags are changed through the tag endpoints)."""

    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    job_title: str | None = Field(default=None, max_length=200)
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    city: str | None = Field(default=None, max_length=100)
    language: str | None = Field(default=None, pattern=r"^[a-z]{2}$")
    timezone: str | None = Field(default=None, max_length=64)
    company_id: uuid.UUID | None = None
    branch_id: uuid.UUID | None = None
    office_id: uuid.UUID | None = None
    stage: ContactStage | None = None
    owner_email: EmailStr | None = None
    network: str | None = Field(default=None, max_length=50, examples=["WCA"])


class TagRequest(StrictRequestModel):
    """A tag to add. Adding a new tag publishes `contact.tag_added`."""

    tag: str = TagField


class ContactResponse(ResponseModel):
    """A contact."""

    id: uuid.UUID
    email: str
    first_name: str | None
    last_name: str | None
    display_name: str
    job_title: str | None
    country: str | None
    city: str | None
    language: str | None
    timezone: str | None
    company_id: uuid.UUID | None
    branch_id: uuid.UUID | None
    office_id: uuid.UUID | None
    stage: ContactStage
    owner_email: str | None
    network: str | None
    tags: list[str]
    email_status: str = Field(
        default="unknown", description="unknown, valid, risky, catch_all or invalid."
    )
    email_verified_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class MessageResponse(ResponseModel):
    """An email exchanged with a contact."""

    id: uuid.UUID
    contact_id: uuid.UUID
    direction: MessageDirection
    kind: MessageKind
    message_id: str
    thread_id: str
    in_reply_to: str | None
    subject: str
    body: str | None
    from_email: str
    to_email: str
    office_id: uuid.UUID | None
    enrollment_id: uuid.UUID | None
    occurred_at: datetime
    processed_at: datetime | None
    processing_result: dict[str, Any] | None
