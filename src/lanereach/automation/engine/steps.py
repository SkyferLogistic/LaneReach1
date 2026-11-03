"""Step helpers shared by side-effecting nodes."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from lanereach.automation.engine.context import ExecutionContext
from lanereach.automation.engine.email import (
    EmailPreparationError,
    ThreadReference,
    choose_sender,
    deliver_email,
    find_template,
    personalise_for_agent,
    render_email,
    template_values,
    thread_reference,
    unsubscribe_url_for,
)
from lanereach.automation.engine.results import Continue, Fail, NodeResult, Stop, Wait
from lanereach.automation.guardrails.base import EmailKind, GuardrailOutcome
from lanereach.automation.workflows.schedule import load_schedule
from lanereach.core.languages import language_name
from lanereach.domain.countries.service import choose_languages, find_country
from lanereach.domain.messages.models import Message, MessageDirection
from lanereach.domain.offices.models import Office
from lanereach.domain.senders.models import ServiceLine
from lanereach.domain.templates.rendering import PLACEHOLDER

SEQUENCE_THREAD_VARIABLE = "sequence_thread"
# A script missing in a country's own language (USA/Canada without the North America script)
# holds the email; it is re-checked this often and goes out once the script exists.
SCRIPT_HOLD_RECHECK = timedelta(hours=12)


async def thread_agent_id(
    services_session: Any, thread: ThreadReference | None
) -> uuid.UUID | None:
    """The agent who last wrote in a thread (follow-ups and replies keep the same person)."""
    if thread is None:
        return None
    sender_id: uuid.UUID | None = await services_session.scalar(
        select(Message.sender_id)
        .where(
            Message.thread_id == thread.thread_id,
            Message.direction == MessageDirection.OUTBOUND,
            Message.sender_id.is_not(None),
        )
        .order_by(Message.occurred_at.desc())
        .limit(1)
    )
    return sender_id


async def send_email_step(
    context: ExecutionContext,
    *,
    kind: EmailKind,
    office: Office,
    template_key: str | None,
    sender_id: uuid.UUID | None = None,
    thread: ThreadReference | None = None,
    prepared_body: str | None = None,
    service_line: ServiceLine = ServiceLine.ANY,
    track: bool = True,
) -> NodeResult:
    """Compose, guard and send one email for the current step.

    Guardrail outcomes map to node results: `block` stops the enrollment (recorded as
    stopped, with the reason), `defer` waits and re-runs this node, `allow` continues. Every
    guardrail decision is stored on the step.

    Args:
        context: The executing step.
        kind: Cold email or reply.
        office: Sending office.
        template_key: Template to render (None when `prepared_body` is given).
        sender_id: Specific sender mailbox, if configured.
        thread: Thread to reply in (replies).
        prepared_body: Ready body (an approved AI draft) instead of a template.
        service_line: Air or ocean agents only; `any` uses the script's service line.
        track: Track opens and clicks (HTML emails, when the tenant tracks).

    Returns:
        The node result.
    """
    services = context.require_services()
    subject_rows = context.require_subject()
    contact = subject_rows.contact
    now = services.clock.now()
    sequence = context.variables.get(SEQUENCE_THREAD_VARIABLE)
    if kind is EmailKind.COLD and isinstance(sequence, dict):
        # A later email of the same sequence continues the first email's thread.
        thread = await thread_reference(
            services.session, str(sequence["thread_id"]), str(sequence["subject"])
        )
        if thread is not None:
            kind = EmailKind.FOLLOW_UP
    session = services.session
    keep_agent = await thread_agent_id(session, thread)
    html_body: str | None = None
    try:
        if prepared_body is not None:
            sender = await choose_sender(
                session,
                office,
                now,
                sender_id,
                service_line=service_line,
                keep_sender_id=keep_agent,
            )
            if sender is None:
                raise EmailPreparationError(f"office '{office.name}' has no active sender mailbox")
            subject = thread.subject if thread else f"Message from {office.name}"
            body = prepared_body
        else:
            if template_key is None:
                raise EmailPreparationError("no template configured")
            country = await find_country(session, contact.country)
            choice = choose_languages(contact, office, country)
            template = await find_template(session, template_key, choice.languages)
            if template is None and choice.strict:
                language = choice.languages[0]
                return Wait(
                    until=now + SCRIPT_HOLD_RECHECK,
                    reason=(
                        f"held: script '{template_key}' has no {language_name(language)} "
                        f"({language}) version yet"
                    ),
                    handle=None,
                )
            if template is None:
                raise EmailPreparationError(
                    f"template '{template_key}' has no version in {', '.join(choice.languages)}"
                )
            line = service_line if service_line is not ServiceLine.ANY else template.service_line
            sender = await choose_sender(
                session, office, now, sender_id, service_line=line, keep_sender_id=keep_agent
            )
            if sender is None:
                agents = "agent" if line is ServiceLine.ANY else f"{line.value} agent"
                raise EmailPreparationError(f"office '{office.name}' has no active {agents}")
            extras = await personalise_for_agent(
                session,
                services.settings,
                tenant=subject_rows.tenant,
                sender=sender,
                template=template,
                country_code=contact.country,
                office=office,
            )
            values = template_values(
                contact=contact,
                company=subject_rows.company,
                office=office,
                sender=sender,
                unsubscribe_url=unsubscribe_url_for(
                    services.settings, contact.tenant_id, contact.id
                ),
                country=choice.country,
                signature=extras.signature,
                signature_text=extras.signature_text,
                banner=extras.banner,
                template_language=template.language,
                office_signature=extras.office_signature,
            )
            rendered = render_email(
                template,
                values,
                subject_override=thread.subject if thread else None,
                settings=services.settings,
            )
            subject, body, html_body = rendered.subject, rendered.body, rendered.html_body
            context.output["template"] = {"key": template.key, "language": template.language}
    except EmailPreparationError as exc:
        return Fail(error=str(exc), retryable=False)

    result = await deliver_email(
        services,
        kind=kind,
        tenant=subject_rows.tenant,
        contact=contact,
        company=subject_rows.company,
        office=office,
        sender=sender,
        subject=subject,
        body=body,
        idempotency_key=context.idempotency_key,
        thread=thread,
        enrollment_id=context.enrollment_id,
        html_body=html_body,
        schedule=load_schedule(subject_rows.workflow.schedule),
        track=track,
    )
    context.guardrail_decisions.extend(decision.to_json() for decision in result.verdict.decisions)
    verdict = result.verdict
    if verdict.outcome is GuardrailOutcome.BLOCK:
        return Stop(
            reason=f"email blocked by {verdict.deciding_guardrail}: {verdict.reason}",
            completed=False,
        )
    if verdict.outcome is GuardrailOutcome.DEFER:
        assert verdict.defer_until is not None  # noqa: S101 - defer always carries a time
        return Wait(
            until=verdict.defer_until,
            reason=f"deferred by {verdict.deciding_guardrail}: {verdict.reason}",
            handle=None,
        )
    assert result.message is not None  # noqa: S101 - allowed deliveries are recorded
    context.output |= {
        "kind": kind.value,
        "message_id": result.message.message_id,
        "thread_id": result.message.thread_id,
        "subject": subject,
        "from": sender.email,
        "to": contact.email,
    }
    if kind is EmailKind.COLD:
        context.variables[SEQUENCE_THREAD_VARIABLE] = {
            "thread_id": result.message.thread_id,
            "subject": subject,
        }
    return Continue()


def notification_values(context: ExecutionContext) -> dict[str, str | None]:
    """Placeholder values available in notification texts.

    Args:
        context: The executing step.

    Returns:
        Placeholder name to value.
    """
    subject = context.require_subject()
    raw_reply = context.variables.get("reply")
    reply: dict[str, Any] = raw_reply if isinstance(raw_reply, dict) else {}
    return {
        "contact.first_name": subject.contact.first_name,
        "contact.full_name": subject.contact.display_name,
        "contact.email": subject.contact.email,
        "company.name": subject.company.name if subject.company else None,
        "office.name": subject.office.name if subject.office else None,
        "workflow.name": subject.workflow.name,
        "reply.bucket": str(reply.get("bucket")) if reply.get("bucket") else None,
        "reply.summary": reply.get("summary"),
    }


def render_notification_text(text: str, values: dict[str, str | None]) -> str:
    """Render a notification text; unknown or empty placeholders become "—".

    Unlike emails, a notification with a missing value is still worth delivering.

    Args:
        text: Text with `{{placeholders}}`.
        values: Available values.

    Returns:
        The rendered text.
    """
    return PLACEHOLDER.sub(
        lambda match: values.get(match.group(1)) or (match.group(2) or "—").strip(), text
    )
