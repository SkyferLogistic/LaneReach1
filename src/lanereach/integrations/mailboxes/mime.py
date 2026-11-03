"""Emails as bytes: build what an agent sends, understand what arrives in their inbox.

Outbound: a standard `multipart/alternative` (text + HTML) with our own `Message-ID`, the
thread headers (`In-Reply-To`, `References`) and the one-click unsubscribe headers.

Inbound: a reply (its `In-Reply-To`/`References` point at our Message-ID) or a delivery status
notification (bounce, RFC 3464). Inbound content is untrusted data: it is only parsed, never
executed or followed, and the text is capped.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser, HeaderParser
from email.utils import format_datetime, formataddr, getaddresses, parseaddr

from lanereach.integrations.sending.models import OutboxMessage

MAX_BODY = 20_000
_PASSED_HEADERS = ("List-Unsubscribe", "List-Unsubscribe-Post")
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")
_BOUNCE_SENDERS = ("mailer-daemon", "postmaster", "mail-daemon")


def _header(value: str) -> str:
    # Header values from the database never carry line breaks into the email (no injection).
    return " ".join(value.replace("\r", " ").replace("\n", " ").split())


def build_outbound_mime(row: OutboxMessage) -> EmailMessage:
    """The email for an outbox row, ready for SMTP.

    Args:
        row: A queued outbox email.

    Returns:
        The MIME message.
    """
    message = EmailMessage(policy=policy.SMTP)
    message["Message-ID"] = row.provider_message_id
    message["Date"] = format_datetime(row.created_at)
    message["From"] = formataddr((_header(row.from_name), row.from_email))
    message["To"] = formataddr((_header(row.to_name or ""), row.to_email))
    message["Subject"] = _header(row.subject)
    if row.in_reply_to:
        message["In-Reply-To"] = row.in_reply_to
        message["References"] = _header(row.references or row.in_reply_to)
    for name in _PASSED_HEADERS:
        value = (row.headers or {}).get(name)
        if isinstance(value, str) and value:
            message[name] = _header(value)
    message.set_content(row.body or "")
    if row.html_body:
        message.add_alternative(row.html_body, subtype="html")
    return message


@dataclass(frozen=True)
class InboundMail:
    """What an inbound email is, reduced to what LaneReach needs."""

    kind: str  # "reply" or "bounce"
    message_id: str | None
    in_reply_to: str | None
    from_email: str
    to_email: str
    subject: str
    body: str
    bounce_type: str | None = None
    original_message_id: str | None = None
    recipient: str | None = None
    diagnostic: str | None = None


def html_to_text(value: str) -> str:
    """Readable text from an HTML body (tags dropped, entities decoded)."""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", value)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", text)
    text = html.unescape(_TAG.sub(" ", text))
    lines = [_SPACE.sub(" ", line).strip() for line in text.splitlines()]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def _text_of(message: Message) -> str:
    if isinstance(message, EmailMessage):
        part = message.get_body(preferencelist=("plain", "html"))
        if part is not None:
            content = part.get_content()
            if not isinstance(content, str):
                return ""
            return html_to_text(content) if part.get_content_type() == "text/html" else content
    payload = message.get_payload(decode=True)
    return payload.decode(errors="replace") if isinstance(payload, bytes) else ""


def _address(value: str | None) -> str:
    return parseaddr(value or "")[1].strip().lower()


def _last_reference(value: str | None) -> str | None:
    ids = re.findall(r"<[^<>\s]+>", value or "")
    return ids[-1] if ids else None


def parse_inbound_mail(raw: bytes) -> InboundMail:
    """Understand an inbound email (a reply or a bounce).

    Args:
        raw: The RFC 5322 bytes as fetched from the mailbox.

    Returns:
        The parsed email.
    """
    message = BytesParser(policy=policy.default).parsebytes(raw)
    subject = _header(str(message.get("Subject", "")))[:300]
    sender = _address(message.get("From"))
    recipients = [address for _, address in getaddresses([str(message.get("To", ""))]) if address]
    to_email = recipients[0].lower() if recipients else ""
    is_report = message.get_content_type() == "multipart/report" and (
        str(message.get_param("report-type", "")).lower() == "delivery-status"
    )
    if is_report or sender.split("@", 1)[0] in _BOUNCE_SENDERS:
        return _parse_bounce(message, subject, sender, to_email)
    in_reply_to = _last_reference(str(message.get("In-Reply-To", ""))) or _last_reference(
        str(message.get("References", ""))
    )
    return InboundMail(
        kind="reply",
        message_id=_last_reference(str(message.get("Message-ID", ""))),
        in_reply_to=in_reply_to,
        from_email=sender,
        to_email=to_email,
        subject=subject,
        body=_text_of(message)[:MAX_BODY].strip(),
    )


def _parse_bounce(message: Message, subject: str, sender: str, to_email: str) -> InboundMail:
    status, recipient, diagnostic, original = "", "", "", None
    for part in message.walk():
        content_type = part.get_content_type()
        if content_type == "message/delivery-status":
            for block in _status_blocks(part):
                status = status or str(block.get("Status", "")).strip()
                recipient = (
                    recipient or str(block.get("Final-Recipient", "")).split(";")[-1].strip()
                )
                diagnostic = (
                    diagnostic or str(block.get("Diagnostic-Code", "")).split(";")[-1].strip()
                )
        elif content_type in ("message/rfc822", "text/rfc822-headers") and original is None:
            headers = _embedded_headers(part)
            original = _last_reference(str(headers.get("Message-ID", ""))) if headers else None
    if original is None:
        original = _last_reference(str(message.get("In-Reply-To", "")))
    # 5.x.x is permanent (the address does not exist); 4.x.x is temporary (full mailbox).
    bounce_type = "soft" if status.startswith("4") else "hard"
    return InboundMail(
        kind="bounce",
        message_id=_last_reference(str(message.get("Message-ID", ""))),
        in_reply_to=None,
        from_email=sender,
        to_email=to_email,
        subject=subject,
        body=(diagnostic or _text_of(message))[:2000],
        bounce_type=bounce_type,
        original_message_id=original,
        recipient=recipient.lower() or None,
        diagnostic=(f"{status} {diagnostic}".strip() or None),
    )


def _status_blocks(part: Message) -> list[Message]:
    payload = part.get_payload()
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Message)]
    text = payload if isinstance(payload, str) else ""
    return [HeaderParser().parsestr(block) for block in re.split(r"\n\s*\n", text) if block.strip()]


def _embedded_headers(part: Message) -> Message | None:
    payload = part.get_payload()
    if isinstance(payload, list) and payload and isinstance(payload[0], Message):
        return payload[0]
    if isinstance(payload, str):
        return HeaderParser().parsestr(payload)
    return None
