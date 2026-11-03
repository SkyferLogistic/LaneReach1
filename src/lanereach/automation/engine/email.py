"""Email delivery used by workflow nodes and the review queue.

`deliver_email` is the only path from LaneReach to a `SendingProvider`. It evaluates the
guardrail chain first and, when allowed, sends, records the message in the conversation,
counts mailbox usage and claims the company-rotation week.
"""

from __future__ import annotations

import html
import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.automation.engine.services import EngineServices
from lanereach.automation.guardrails.base import EmailKind, GuardrailOutcome, OutboundEmail
from lanereach.automation.guardrails.chain import SendVerdict, evaluate_send_guardrails
from lanereach.automation.guardrails.company_rotation import iso_week_of
from lanereach.automation.guardrails.mailbox_capacity import office_day
from lanereach.automation.workflows.schedule import CampaignSchedule
from lanereach.core.config import Settings
from lanereach.core.tenancy import require_session_tenant_id
from lanereach.domain.assets.service import asset_url, find_banner, resolve_asset_references
from lanereach.domain.companies.models import Company, CompanyOfficeStart
from lanereach.domain.contacts.models import Contact, ContactStage
from lanereach.domain.countries.models import Country
from lanereach.domain.countries.service import destination_phrase
from lanereach.domain.messages.models import Message, MessageDirection, MessageKind
from lanereach.domain.offices.models import Office
from lanereach.domain.senders.models import MailboxDailyUsage, Sender, ServiceLine
from lanereach.domain.senders.signature import (
    build_signature_html,
    build_signature_text,
    signature_image_html,
)
from lanereach.domain.templates.models import Template, TemplateStatus
from lanereach.domain.templates.rendering import SafeHtml, render_template_text
from lanereach.integrations.sending.provider import OutboundMessage, ProviderMessageRef
from lanereach.integrations.sending.unsubscribe import create_unsubscribe_token
from lanereach.integrations.tracking.tokens import add_tracking
from lanereach.tenants.branding import load_branding
from lanereach.tenants.models import Tenant

_MESSAGE_KIND = {
    EmailKind.COLD: MessageKind.COLD,
    EmailKind.FOLLOW_UP: MessageKind.FOLLOW_UP,
    EmailKind.REPLY: MessageKind.REPLY,
    EmailKind.APPROVED_REPLY: MessageKind.REPLY,
}


class EmailPreparationError(Exception):
    """The email cannot be built (missing template, sender, thread or placeholder value).

    These are configuration problems: retrying will not help, so nodes turn them into
    non-retryable failures that are visible on the enrollment.
    """


@dataclass(frozen=True)
class ThreadReference:
    """The conversation a reply belongs to."""

    thread_id: str
    in_reply_to: str
    references: tuple[str, ...]
    subject: str


@dataclass(frozen=True)
class DeliveryResult:
    """Outcome of `deliver_email`."""

    verdict: SendVerdict
    message: Message | None = None
    provider_ref: ProviderMessageRef | None = None

    @property
    def sent(self) -> bool:
        """Whether the email was handed to the provider."""
        return self.provider_ref is not None


def unsubscribe_url_for(settings: Settings, tenant_id: uuid.UUID, contact_id: uuid.UUID) -> str:
    """Return the one-click unsubscribe link for a contact.

    Args:
        settings: Supplies the public base URL and signing secret.
        tenant_id: Tenant of the contact.
        contact_id: The contact.

    Returns:
        The absolute URL.
    """
    token = create_unsubscribe_token(
        settings.unsubscribe_signing_secret.get_secret_value(), tenant_id, contact_id
    )
    return f"{settings.public_base_url}/api/v1/unsubscribe/{token}"


async def find_template(
    session: AsyncSession, key: str, languages: list[str], *, include_drafts: bool = False
) -> Template | None:
    """Return the first template version available in the preferred languages.

    Only `active` versions are returned (an AI translation waiting for approval, an inactive
    or broken import is never sent) unless `include_drafts` asks for any version.

    Args:
        session: Tenant session.
        key: Template key.
        languages: Language codes in order of preference.
        include_drafts: Also return non-active versions (existence checks).

    Returns:
        The template, or None if no language version exists.
    """
    statement = select(Template).where(Template.key == key, Template.language.in_(languages))
    if not include_drafts:
        statement = statement.where(Template.status == TemplateStatus.ACTIVE)
    rows = (await session.scalars(statement)).all()
    by_language = {row.language: row for row in rows}
    for language in languages:
        if language in by_language:
            return by_language[language]
    return None


def preferred_languages(contact: Contact, office: Office) -> list[str]:
    """Contact language, then office default, then English (deduplicated).

    Args:
        contact: Recipient.
        office: Sending office.

    Returns:
        Language codes in order of preference.
    """
    ordered = [contact.language, office.default_language, "en"]
    return list(dict.fromkeys(code for code in ordered if code))


def template_values(
    *,
    contact: Contact,
    company: Company | None,
    office: Office,
    sender: Sender,
    unsubscribe_url: str,
    country: Country | None = None,
    signature: SafeHtml | None = None,
    signature_text: str | None = None,
    banner: SafeHtml | None = None,
    template_language: str | None = None,
    office_signature: SafeHtml | None = None,
) -> dict[str, str | None]:
    """Values available to `{{placeholders}}`.

    Args:
        contact: Recipient.
        company: Recipient company.
        office: Sending office.
        sender: Sending mailbox (the agent).
        unsubscribe_url: One-click unsubscribe link.
        country: The contact's destination-country row (`{to_country}` phrase).
        signature: The agent's HTML signature.
        signature_text: The agent's plain-text signature.
        banner: The country banner as HTML.
        template_language: Language of the script, so `{to_country}` matches it (the
            country's own phrase is only used in its own language).
        office_signature: The office's signature picture as HTML, when it has one.

    Returns:
        Placeholder name to value.
    """
    to_phrase = None
    if country is not None:
        to_phrase = destination_phrase(country, template_language or country.language)
    return {
        "country.name": country.name if country else None,
        "country.to_phrase": to_phrase,
        "contact.network": contact.network,
        "sender.title": sender.job_title,
        "sender.phone": sender.phone,
        "sender.signature": signature,
        "sender.signature_text": signature_text,
        "banner.html": banner,
        "contact.first_name": contact.first_name,
        "contact.last_name": contact.last_name,
        "contact.full_name": contact.display_name,
        "contact.email": contact.email,
        "contact.job_title": contact.job_title,
        "contact.city": contact.city,
        "contact.country": contact.country,
        "company.name": company.name if company else None,
        "office.name": office.name,
        "office.owner_name": office.owner_name,
        "office.signature": office.signature or None,
        "office.signature_html": office_signature,
        "sender.name": sender.display_name,
        "sender.first_name": sender.display_name.split(" ", 1)[0],
        "sender.email": sender.email,
        "unsubscribe.url": unsubscribe_url,
    }


async def _connected_senders(session: AsyncSession, sender_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """The agents among `sender_ids` whose mailbox is connected and verified."""
    if not sender_ids:
        return set()
    from lanereach.integrations.mailboxes.models import MailboxStatus, SenderMailbox

    rows = await session.scalars(
        select(SenderMailbox.sender_id).where(
            SenderMailbox.sender_id.in_(sender_ids), SenderMailbox.status == MailboxStatus.VERIFIED
        )
    )
    return set(rows.all())


async def choose_sender(
    session: AsyncSession,
    office: Office,
    now: datetime,
    preferred_sender_id: uuid.UUID | None,
    *,
    service_line: ServiceLine = ServiceLine.ANY,
    keep_sender_id: uuid.UUID | None = None,
) -> Sender | None:
    """Pick the sender mailbox (agent) for an email.

    A configured sender is used if active. Otherwise the thread's agent is kept when still
    usable (a contact talks to the same person for the whole conversation), else the office's
    active, unflagged agent of the right service line with the fewest emails today.

    Args:
        session: Tenant session.
        office: Sending office.
        now: Current time.
        preferred_sender_id: Sender chosen in the node config, if any (strict).
        service_line: Air or ocean agents only (`any` allows every agent).
        keep_sender_id: Agent of the conversation so far, preferred when usable.

    Returns:
        The sender, or None when the office has no usable mailbox.
    """
    statement = select(Sender).where(
        Sender.office_id == office.id,
        Sender.is_active.is_(True),
        Sender.flagged_at.is_(None),
    )
    if service_line is not ServiceLine.ANY:
        statement = statement.where(Sender.service_line.in_([service_line, ServiceLine.ANY]))
    candidates = list((await session.scalars(statement)).all())
    connected = await _connected_senders(session, [sender.id for sender in candidates])
    if connected:
        # Once any agent of the office has a working mailbox, only those agents send: the others
        # would be deferred by the mailbox guardrail in SMTP mode.
        candidates = [sender for sender in candidates if sender.id in connected]
    if preferred_sender_id is not None:
        return next((sender for sender in candidates if sender.id == preferred_sender_id), None)
    if keep_sender_id is not None:
        kept = next((sender for sender in candidates if sender.id == keep_sender_id), None)
        if kept is not None:
            return kept
    if not candidates:
        return None
    today = office_day(now, office.timezone)
    usage_rows = await session.execute(
        select(MailboxDailyUsage.sender_id, MailboxDailyUsage.sent_count).where(
            MailboxDailyUsage.sender_id.in_([sender.id for sender in candidates]),
            MailboxDailyUsage.day == today,
        )
    )
    usage = {sender_id: sent for sender_id, sent in usage_rows.all()}
    return min(candidates, key=lambda sender: (usage.get(sender.id, 0), sender.email))


async def thread_reference(
    session: AsyncSession, thread_id: str, subject: str
) -> ThreadReference | None:
    """Build a reference to reply at the end of a known thread.

    Args:
        session: Tenant session.
        thread_id: Thread ID (Message-ID of its first email).
        subject: Subject of the thread's first email.

    Returns:
        The reference, or None if the thread has no messages.
    """
    thread = (
        await session.scalars(
            select(Message.message_id)
            .where(Message.thread_id == thread_id)
            .order_by(Message.occurred_at)
        )
    ).all()
    if not thread:
        return None
    reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    return ThreadReference(thread_id, thread[-1], tuple(thread), reply_subject)


async def find_reply_thread(session: AsyncSession, contact: Contact) -> ThreadReference | None:
    """Find the latest conversation with a contact, to reply in it.

    Args:
        session: Tenant session.
        contact: The contact.

    Returns:
        The thread reference, or None if LaneReach never exchanged email with the contact.
    """
    latest = await session.scalar(
        select(Message)
        .where(
            Message.contact_id == contact.id,
            or_(Message.kind == MessageKind.REPLY, Message.kind == MessageKind.COLD),
        )
        .order_by(Message.occurred_at.desc())
        .limit(1)
    )
    if latest is None:
        return None
    thread = (
        await session.scalars(
            select(Message.message_id)
            .where(Message.thread_id == latest.thread_id)
            .order_by(Message.occurred_at)
        )
    ).all()
    subject = (
        latest.subject if latest.subject.lower().startswith("re:") else f"Re: {latest.subject}"
    )
    return ThreadReference(latest.thread_id, latest.message_id, tuple(thread), subject)


@dataclass(frozen=True)
class RenderedEmail:
    """A rendered email: subject, plain-text body and (for HTML scripts) the HTML body."""

    subject: str
    body: str
    html_body: str | None


def render_email(
    template: Template,
    values: dict[str, str | None],
    *,
    subject_override: str | None = None,
    settings: Settings | None = None,
    lenient: bool = False,
) -> RenderedEmail:
    """Render a template's subject, text body and HTML body.

    Args:
        template: The template version.
        values: Placeholder values.
        subject_override: Subject to use instead (replies keep "Re: <original>").
        settings: Needed to turn stored image references into public links (HTML scripts).
        lenient: Show missing values as `[name]` (previews) instead of failing.

    Returns:
        The rendered email.

    Raises:
        EmailPreparationError: If a placeholder has no value and no fallback.
    """
    try:
        subject = subject_override or render_template_text(
            template.subject, values, lenient=lenient
        )
        body = render_template_text(template.body, values, lenient=lenient)
        if template.html_body:
            html_body: str | None = render_template_text(
                template.html_body, values, html_mode=True, lenient=lenient
            )
        elif isinstance(values.get("office.signature_html"), SafeHtml):
            # A plain template of an office with a signature picture: send HTML too, so the
            # email can end with the picture.
            html_body = render_template_text(
                text_template_to_html(template.body), values, html_mode=True, lenient=lenient
            )
        else:
            html_body = None
    except ValueError as exc:
        raise EmailPreparationError(
            f"template '{template.key}' ({template.language}): {exc}"
        ) from exc
    if html_body and settings is not None:
        html_body = resolve_asset_references(html_body, settings, template.tenant_id)
    return RenderedEmail(subject, body, html_body)


def text_template_to_html(text: str) -> str:
    """A plain-text template as an HTML template ending with the office signature picture.

    Text is escaped, paragraphs are kept and `{{office.signature}}` becomes the picture (it is
    appended when the template has no signature placeholder).

    Placeholders survive escaping (braces and dots are not escaped); their values are escaped
    when rendered in HTML mode.
    """
    # The picture is a block of its own, never inside a paragraph.
    escaped = html.escape(text, quote=False).replace(
        "{{office.signature}}", "\n\n{{office.signature_html}}\n\n"
    )
    if "{{office.signature_html}}" not in escaped:
        escaped = f"{escaped.rstrip()}\n\n{{{{office.signature_html}}}}"
    paragraphs = [part for part in re.split(r"\n\s*\n", escaped.strip()) if part.strip()]
    blocks = [
        part.strip()
        if part.strip() == "{{office.signature_html}}"
        else '<p style="margin:0 0 12px 0;">' + part.strip().replace("\n", "<br>") + "</p>"
        for part in paragraphs
    ]
    return (
        '<div style="font-family:Calibri,Arial,Helvetica,sans-serif;font-size:14px;'
        'line-height:1.5;color:#1f2933;max-width:640px;">' + "".join(blocks) + "</div>"
    )


@dataclass(frozen=True)
class AgentPersonalisation:
    """Values that need the database: signatures and the country banner."""

    signature: SafeHtml
    signature_text: str
    banner: SafeHtml | None
    office_signature: SafeHtml | None = None


async def personalise_for_agent(
    session: AsyncSession,
    settings: Settings,
    *,
    tenant: Tenant,
    sender: Sender,
    template: Template | None,
    country_code: str | None,
    office: Office | None = None,
) -> AgentPersonalisation:
    """Build the agent's signature and pick the banner for the contact's country.

    Args:
        session: Tenant session.
        settings: Public URL and signing secret for image links.
        tenant: The tenant (branding).
        sender: The agent.
        template: The script (service line decides the banner set).
        country_code: The contact's country.
        office: The sending office (its signature picture, if any).

    Returns:
        Signatures (HTML and text) and banner.
    """
    branding = load_branding(tenant.branding)

    def link(asset_id: uuid.UUID) -> str:
        return asset_url(settings, tenant.id, asset_id)

    signature = build_signature_html(sender, branding, tenant.name, link)
    banner_html: SafeHtml | None = None
    if template is not None and template.html_body and "banner.html" in template.html_body:
        banner = await find_banner(session, template.service_line, country_code)
        if banner is not None:
            banner_html = SafeHtml(
                f'<p style="margin:12px 0;"><img src="{link(banner.id)}" alt="" width="600" '
                f'style="display:block;max-width:100%;height:auto;border:0;"></p>'
            )
    office_signature = (
        SafeHtml(signature_image_html(link(office.signature_asset_id), office.name))
        if office is not None and office.signature_asset_id
        else None
    )
    return AgentPersonalisation(
        signature,
        build_signature_text(sender, branding, tenant.name),
        banner_html,
        office_signature,
    )


async def deliver_email(
    services: EngineServices,
    *,
    kind: EmailKind,
    tenant: Tenant,
    contact: Contact,
    company: Company | None,
    office: Office,
    sender: Sender | None,
    subject: str,
    body: str,
    idempotency_key: str,
    thread: ThreadReference | None = None,
    enrollment_id: uuid.UUID | None = None,
    html_body: str | None = None,
    schedule: CampaignSchedule | None = None,
    track: bool = True,
) -> DeliveryResult:
    """Run the guardrails and, if allowed, send and record the email.

    Args:
        services: Engine services (session, clock, provider).
        kind: Which guardrails apply.
        tenant: Sending tenant (kill switch, window).
        contact: Recipient.
        company: Recipient company (freeze, rotation).
        office: Sending office.
        sender: Sending mailbox (None when none is available; capacity guardrail defers).
        subject: Rendered subject.
        body: Rendered plain-text body.
        idempotency_key: Stable key of the step or review decision.
        thread: Thread to reply in (replies only).
        enrollment_id: Enrollment that sends, if any.
        html_body: Rendered HTML body (HTML scripts).
        schedule: The sending workflow's campaign schedule.
        track: Track opens and clicks when the tenant does (HTML emails only).

    Returns:
        The verdict and, when sent, the recorded message.
    """
    verdict = await evaluate_send_guardrails(
        services.guardrail_context(),
        OutboundEmail(
            kind=kind,
            tenant=tenant,
            contact=contact,
            company=company,
            office=office,
            sender=sender,
            schedule=schedule,
        ),
    )
    if verdict.outcome is not GuardrailOutcome.ALLOW:
        return DeliveryResult(verdict)
    assert sender is not None  # noqa: S101 - the capacity guardrail defers without a sender

    session = services.session
    now = services.clock.now()
    unsubscribe_url = unsubscribe_url_for(services.settings, tenant.id, contact.id)
    tracking_id: uuid.UUID | None = None
    if track and html_body and (tenant.track_opens or tenant.track_clicks):
        # Plain-text emails cannot carry a pixel or rewritten links; they stay untracked.
        tracking_id = uuid.uuid4()
        html_body = add_tracking(
            html_body,
            services.settings,
            tenant.id,
            tracking_id,
            opens=tenant.track_opens,
            clicks=tenant.track_clicks,
        )
    outbound = OutboundMessage(
        idempotency_key=idempotency_key,
        kind=_MESSAGE_KIND[kind].value,
        to_email=contact.email,
        to_name=contact.display_name,
        from_email=sender.email,
        from_name=sender.display_name,
        subject=subject,
        body=body,
        html_body=html_body,
        thread_id=thread.thread_id if thread else None,
        in_reply_to=thread.in_reply_to if thread else None,
        references=thread.references if thread else (),
        headers={
            "List-Unsubscribe": f"<{unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
        contact_id=contact.id,
        office_id=office.id,
        sender_id=sender.id,
        enrollment_id=enrollment_id,
    )
    provider = services.sending_provider
    ref = (
        await provider.send_reply_in_thread(outbound)
        if thread
        else await provider.send_email(outbound)
    )
    message = await _record_outbound_message(session, outbound, ref, now, tracking_id)
    await _count_mailbox_use(session, sender, office, now)
    if kind is EmailKind.COLD:
        if company is not None:
            await _claim_rotation_week(session, company, office, now)
        if contact.stage is ContactStage.NEW:
            contact.stage = ContactStage.CONTACTED
            contact.updated_at = now
    return DeliveryResult(verdict, message, ref)


async def _record_outbound_message(
    session: AsyncSession,
    outbound: OutboundMessage,
    ref: ProviderMessageRef,
    now: datetime,
    tracking_id: uuid.UUID | None = None,
) -> Message:
    existing = await session.scalar(select(Message).where(Message.message_id == ref.message_id))
    if existing is not None:
        return existing
    message = Message(
        tenant_id=require_session_tenant_id(session),
        contact_id=outbound.contact_id,
        direction=MessageDirection.OUTBOUND,
        kind=MessageKind(outbound.kind),
        message_id=ref.message_id,
        thread_id=ref.thread_id,
        in_reply_to=outbound.in_reply_to,
        subject=outbound.subject,
        body=outbound.body,
        from_email=outbound.from_email,
        to_email=outbound.to_email,
        office_id=outbound.office_id,
        sender_id=outbound.sender_id,
        enrollment_id=outbound.enrollment_id,
        provider=ref.provider,
        occurred_at=ref.sent_at,
        tracking_id=tracking_id,
        created_at=now,
    )
    session.add(message)
    await session.flush()
    return message


async def _count_mailbox_use(
    session: AsyncSession, sender: Sender, office: Office, now: datetime
) -> None:
    await session.execute(
        insert(MailboxDailyUsage)
        .values(
            id=uuid.uuid4(),
            tenant_id=require_session_tenant_id(session),
            sender_id=sender.id,
            day=office_day(now, office.timezone),
            sent_count=1,
            created_at=now,
        )
        .on_conflict_do_update(
            index_elements=["tenant_id", "sender_id", "day"],
            set_={"sent_count": MailboxDailyUsage.sent_count + 1},
        )
    )


async def _claim_rotation_week(
    session: AsyncSession, company: Company, office: Office, now: datetime
) -> None:
    year, week = iso_week_of(now)
    await session.execute(
        insert(CompanyOfficeStart)
        .values(
            id=uuid.uuid4(),
            tenant_id=require_session_tenant_id(session),
            company_id=company.id,
            office_id=office.id,
            iso_year=year,
            iso_week=week,
            created_at=now,
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "company_id", "iso_year", "iso_week"])
    )
