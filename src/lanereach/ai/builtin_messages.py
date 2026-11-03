"""Built-in messages: answers to replies and quiet-deal follow-ups written without an AI call.

A company's own message comes first: its `reply_<bucket>` template (or `follow_up_quiet`) in
the contact's language, then the office's language, then English. Without one, LaneReach's own
English text below is used. Either way the result is a draft a person reviews like an AI draft,
and the same words every time, which suits replies that need no thinking (a meeting request, a
thank-you for a referral, the first answer to a rate request while pricing works on it).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.ai.schemas import ReplyBucket
from lanereach.automation.engine.email import find_template
from lanereach.domain.templates.rendering import TemplateRenderError, render_template_text

FOLLOW_UP_TEMPLATE_KEY = "follow_up_quiet"
_SIGN_OFF = "\n\nBest regards,\n{{sender.name}}\n{{office.name}}"

BUILT_IN_REPLIES: dict[ReplyBucket, str] = {
    ReplyBucket.INTERESTED: (
        "Hi {{contact.first_name|there}},\n\n"
        "Thank you for getting back to us. We would be glad to help with your shipments. "
        "Could you share the lanes you ship most, the cargo and the usual volumes? "
        "We will come back to you with options." + _SIGN_OFF
    ),
    ReplyBucket.MEETING_REQUEST: (
        "Hi {{contact.first_name|there}},\n\n"
        "Thank you, happy to set up a call. Which days and times suit you this week or next? "
        "Let me know and I will send an invitation." + _SIGN_OFF
    ),
    ReplyBucket.RATE_QUERY: (
        "Hi {{contact.first_name|there}},\n\n"
        "Thank you for your rate request. Our pricing team is preparing the quote and will "
        "send it to you shortly. If you can, please confirm the cargo ready date, the commodity "
        "and the Incoterm so the quote is exact." + _SIGN_OFF
    ),
    ReplyBucket.SERVICE_QUERY: (
        "Hi {{contact.first_name|there}},\n\n"
        "Thank you for your question. A colleague who knows this service well will answer you "
        "in detail shortly." + _SIGN_OFF
    ),
    ReplyBucket.REFERRAL: (
        "Hi {{contact.first_name|there}},\n\n"
        "Thank you for pointing us to the right person; we will reach out to them directly."
        + _SIGN_OFF
    ),
}
GENERIC_REPLY = (
    "Hi {{contact.first_name|there}},\n\n"
    "Thank you for your message. Could you tell me a little more about what you need, so I "
    "can help you best?" + _SIGN_OFF
)
BUILT_IN_FOLLOW_UP = (
    "Hi {{contact.first_name|there}},\n\n"
    "I wanted to check in on {{deal.lane|your shipment}}. Is there anything you need from us "
    "to move ahead: an updated rate, another routing or a different date?" + _SIGN_OFF
)


@dataclass(frozen=True)
class BuiltInMessage:
    """A message written from a template, not by the AI.

    Attributes:
        subject: Subject line.
        body: Plain-text body.
        language: Language the message is in.
        template_key: The company's template used, or None for LaneReach's own text.
        note: Shown to the reviewer, for example when the contact's language had no version.
    """

    subject: str
    body: str
    language: str
    template_key: str | None
    note: str | None = None


def reply_subject(inbound_subject: str) -> str:
    """`Re: <subject>` so the answer stays in the customer's thread."""
    subject = inbound_subject.strip() or "Your message"
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"[:300]


def message_values(
    *,
    contact_first_name: str | None,
    contact_full_name: str | None,
    company_name: str | None,
    office_name: str | None,
    office_owner_name: str | None,
    sender_name: str | None,
    extra: Mapping[str, str | None] | None = None,
) -> dict[str, str | None]:
    """The placeholder values a built-in message may use."""
    sender = sender_name or office_owner_name or office_name or ""
    values: dict[str, str | None] = {
        "contact.first_name": contact_first_name,
        "contact.full_name": contact_full_name,
        "company.name": company_name,
        "office.name": office_name,
        "office.owner_name": office_owner_name,
        "sender.name": sender,
        "sender.first_name": sender.split(" ", 1)[0] if sender else None,
    }
    values.update(extra or {})
    return values


async def write_built_in_message(
    session: AsyncSession,
    *,
    template_key: str,
    default_body: str,
    default_subject: str,
    languages: list[str],
    values: Mapping[str, str | None],
    thread_subject: str | None = None,
) -> BuiltInMessage:
    """Fill the company's template for `template_key`, else LaneReach's default text.

    Args:
        session: Tenant session.
        template_key: Template key to look for.
        default_body: LaneReach's English text when the company has none (or it cannot be
            filled, for example a placeholder only outreach emails have).
        default_subject: Subject of the default text.
        languages: Preferred languages, the contact's first.
        values: Placeholder values.
        thread_subject: Keep this subject (a reply stays in its thread) instead of the
            template's.

    Returns:
        The message.
    """
    wanted = languages[0] if languages else "en"
    template = await find_template(session, template_key, [*dict.fromkeys([*languages, "en"])])
    if template is not None:
        try:
            body = render_template_text(template.body, values)
            subject = thread_subject or render_template_text(template.subject, values)
        except TemplateRenderError:
            template = None
    if template is None:
        body = render_template_text(default_body, values)
        subject, language, key = thread_subject or default_subject, "en", None
    else:
        language, key = template.language, template.key
    note = None
    if language != wanted:
        note = (
            f"No '{wanted}' version of this message, so it is in '{language}'. Add one in "
            f"Templates as '{template_key}' (Translate with AI writes it once)."
        )
    return BuiltInMessage(subject[:300], body, language, key, note)


async def write_built_in_reply(
    session: AsyncSession,
    *,
    bucket: ReplyBucket,
    languages: list[str],
    inbound_subject: str,
    values: Mapping[str, str | None],
) -> BuiltInMessage:
    """The built-in answer to a reply of `bucket` (the company's `reply_<bucket>` template)."""
    return await write_built_in_message(
        session,
        template_key=f"reply_{bucket.value}",
        default_body=BUILT_IN_REPLIES.get(bucket, GENERIC_REPLY),
        default_subject=reply_subject(inbound_subject),
        languages=languages,
        values=values,
        thread_subject=reply_subject(inbound_subject),
    )
