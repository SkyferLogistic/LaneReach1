"""Web forms: managing them and turning a submission into a contact, a tag and an event.

The public endpoint is on the open internet, so a submission can only create or update a
contact (never delete or read anything), a hidden `website` field catches bots (a filled
honeypot is accepted and silently dropped, so bots learn nothing), and rate limits apply per
address and per form. No CAPTCHA is solved or required.
"""

from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lanereach.automation.engine.queue import TaskQueue
from lanereach.automation.events.models import EventType
from lanereach.automation.events.service import publish_event
from lanereach.core.clock import Clock
from lanereach.core.errors import BusinessRuleViolationError, NotFoundError, RateLimitedError
from lanereach.core.tenancy import open_system_session, require_session_tenant_id
from lanereach.domain.contacts.models import Contact
from lanereach.domain.contacts.schemas import ContactCreateRequest
from lanereach.domain.contacts.service import ContactService
from lanereach.domain.forms.models import FormSubmission, WebForm
from lanereach.domain.offices.models import Office

HONEYPOT = "website"
# Fields a quote form can ask for, in the order the embed snippet shows them.
FORM_FIELDS: dict[str, str] = {
    "first_name": "First name",
    "last_name": "Last name",
    "company": "Company",
    "phone": "Phone",
    "country": "Country (2 letters)",
    "origin": "Origin (city / port / airport)",
    "destination": "Destination",
    "mode": "Mode (air, ocean FCL, ocean LCL)",
    "commodity": "Commodity",
    "weight": "Weight / volume",
    "ready_date": "Cargo ready date",
    "message": "Message",
}
MAX_VALUE = 2000
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")
_TAG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True, slots=True)
class FormInput:
    """A form's settings."""

    name: str
    office_id: uuid.UUID | None
    tag: str | None
    fields: list[str]
    redirect_url: str | None
    thank_you_text: str
    is_active: bool = True


def _check(data: FormInput) -> FormInput:
    unknown = [item for item in data.fields if item not in FORM_FIELDS]
    if unknown:
        raise BusinessRuleViolationError(f"Unknown fields: {', '.join(unknown)}")
    if data.tag and not _TAG.match(data.tag):
        raise BusinessRuleViolationError("Tags are lower-case letters, digits, - and _.")
    if data.redirect_url and not data.redirect_url.startswith("https://"):
        raise BusinessRuleViolationError("The thank-you page must be an https:// address.")
    return data


class FormService:
    """Forms of the tenant.

    Args:
        session: Tenant session.
        clock: Time source.
    """

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    async def list_forms(self) -> list[WebForm]:
        """Forms, by name."""
        return list((await self._session.scalars(select(WebForm).order_by(WebForm.name))).all())

    async def require_form(self, form_id: uuid.UUID) -> WebForm:
        """A form, or 404."""
        form = await self._session.get(WebForm, form_id)
        if form is None:
            raise NotFoundError("Form not found.")
        return form

    async def _check_office(self, office_id: uuid.UUID | None) -> None:
        if office_id and await self._session.get(Office, office_id) is None:
            raise NotFoundError("Office not found.")

    async def create_form(self, data: FormInput, actor: str) -> WebForm:
        """Create a form with a fresh public key."""
        data = _check(data)
        await self._check_office(data.office_id)
        now = self._clock.now()
        form = WebForm(
            tenant_id=require_session_tenant_id(self._session),
            name=data.name.strip()[:100],
            public_key="f_" + secrets.token_urlsafe(16),
            office_id=data.office_id,
            tag=data.tag,
            fields=list(dict.fromkeys(data.fields)),
            redirect_url=data.redirect_url,
            thank_you_text=data.thank_you_text.strip()[:1000],
            is_active=data.is_active,
            submission_count=0,
            created_by=actor,
            created_at=now,
            updated_at=now,
        )
        self._session.add(form)
        await self._session.flush()
        return form

    async def update_form(self, form_id: uuid.UUID, data: FormInput) -> WebForm:
        """Replace a form's settings (the public key stays)."""
        form = await self.require_form(form_id)
        data = _check(data)
        await self._check_office(data.office_id)
        form.name = data.name.strip()[:100]
        form.office_id = data.office_id
        form.tag = data.tag
        form.fields = list(dict.fromkeys(data.fields))
        form.redirect_url = data.redirect_url
        form.thank_you_text = data.thank_you_text.strip()[:1000]
        form.is_active = data.is_active
        form.updated_at = self._clock.now()
        await self._session.flush()
        return form

    async def delete_form(self, form_id: uuid.UUID) -> None:
        """Remove a form and its submissions."""
        await self._session.delete(await self.require_form(form_id))
        await self._session.flush()

    async def list_submissions(self, form_id: uuid.UUID, limit: int = 100) -> list[FormSubmission]:
        """Recent submissions of a form."""
        await self.require_form(form_id)
        return list(
            (
                await self._session.scalars(
                    select(FormSubmission)
                    .where(FormSubmission.form_id == form_id)
                    .order_by(FormSubmission.created_at.desc())
                    .limit(limit)
                )
            ).all()
        )


async def find_form_tenant(
    session_factory: async_sessionmaker[AsyncSession], public_key: str
) -> uuid.UUID | None:
    """The tenant owning a public form key (looked up without a tenant, then used for one).

    Args:
        session_factory: Session factory.
        public_key: Key from the form's URL.

    Returns:
        The tenant id, or None.
    """
    async with open_system_session(session_factory) as system:
        tenant_id: uuid.UUID | None = await system.scalar(
            text("SELECT tenant_id FROM web_form_owner(:key)"), {"key": public_key}
        )
    return tenant_id


@dataclass(frozen=True, slots=True)
class SubmissionOutcome:
    """What a submission did."""

    form: WebForm
    accepted: bool
    contact_id: uuid.UUID | None = None


def _clean_answers(form: WebForm, raw: dict[str, Any]) -> dict[str, str]:
    answers: dict[str, str] = {}
    for key in form.fields:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            answers[key] = (
                " ".join(value.split())[:MAX_VALUE]
                if key != "message"
                else value.strip()[:MAX_VALUE]
            )
    return answers


async def submit_form(
    session: AsyncSession,
    clock: Clock,
    public_key: str,
    raw: dict[str, Any],
    *,
    task_queue: TaskQueue | None,
    per_email_per_hour: int,
    per_form_per_minute: int,
) -> SubmissionOutcome:
    """Handle a public submission.

    Creates the contact (or fills empty name fields of an existing one), adds the form's tag,
    stores the submission and publishes `form.submitted` (webhooks, Slack, workflows).

    Args:
        session: Tenant session of the form's tenant.
        clock: Time source.
        public_key: The form's key.
        raw: Submitted fields.
        task_queue: Where event processing is enqueued.
        per_email_per_hour: Rate limit per address.
        per_form_per_minute: Rate limit per form.

    Returns:
        The outcome (`accepted=False` for a filled honeypot).

    Raises:
        NotFoundError: For an unknown or switched-off form.
        BusinessRuleViolationError: For a missing or invalid email.
        RateLimitedError: When the limits are exceeded.
    """
    form = await session.scalar(select(WebForm).where(WebForm.public_key == public_key))
    if form is None or not form.is_active:
        raise NotFoundError("This form is not available.")
    if str(raw.get(HONEYPOT) or "").strip():
        return SubmissionOutcome(form, accepted=False)
    email = str(raw.get("email") or "").strip().lower()
    if not _EMAIL.match(email) or len(email) > 320:
        raise BusinessRuleViolationError("Please enter a valid email address.")
    now = clock.now()
    recent_form = await session.scalar(
        select(func.count()).where(
            FormSubmission.form_id == form.id,
            FormSubmission.created_at >= now - timedelta(minutes=1),
        )
    )
    recent_email = await session.scalar(
        select(func.count()).where(
            FormSubmission.email == email, FormSubmission.created_at >= now - timedelta(hours=1)
        )
    )
    if (recent_form or 0) >= per_form_per_minute or (recent_email or 0) >= per_email_per_hour:
        raise RateLimitedError("Too many requests. Please try again in a few minutes.")
    answers = _clean_answers(form, raw)
    contacts = ContactService(session, clock, task_queue)
    contact = await session.scalar(select(Contact).where(Contact.email == email))
    if contact is None:
        country = answers.get("country", "").upper()
        contact = await contacts.create_contact(
            ContactCreateRequest(
                email=email,
                first_name=answers.get("first_name", "")[:100] or None,
                last_name=answers.get("last_name", "")[:100] or None,
                country=country if re.fullmatch(r"[A-Z]{2}", country) else None,
                office_id=form.office_id,
            )
        )
    else:
        contact.first_name = contact.first_name or (answers.get("first_name", "")[:100] or None)
        contact.last_name = contact.last_name or (answers.get("last_name", "")[:100] or None)
        contact.updated_at = now
    if form.tag and form.tag not in contact.tags:
        await contacts.add_tag(contact.id, form.tag)
    session.add(
        FormSubmission(
            tenant_id=form.tenant_id,
            form_id=form.id,
            contact_id=contact.id,
            email=email,
            data=answers,
            created_at=now,
        )
    )
    form.submission_count += 1
    form.last_submission_at = now
    await session.flush()
    await publish_event(
        session,
        clock,
        event_type=EventType.FORM_SUBMITTED,
        contact_id=contact.id,
        payload={
            "form_id": str(form.id),
            "form": form.name,
            # The message is personal text: summarised to its start, never logged.
            "answers": {key: value[:300] for key, value in answers.items()},
        },
        idempotency_key=f"form:{form.id}:{contact.id}:{now.isoformat()}",
        task_queue=task_queue,
    )
    return SubmissionOutcome(form, accepted=True, contact_id=contact.id)
