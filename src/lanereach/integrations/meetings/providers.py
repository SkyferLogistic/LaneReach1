"""Verifying and reading Calendly and Cal.com webhooks.

* Calendly signs with `Calendly-Webhook-Signature: t=<unix>,v1=<hex HMAC-SHA256 of "<t>.<body>">`
  using the webhook's signing key. Requests older than five minutes are refused (replays).
* Cal.com signs with `X-Cal-Signature-256: <hex HMAC-SHA256 of the body>` using the webhook
  secret.

The body is parsed only after the signature matched, and only the fields LaneReach needs are
read; everything else in the payload is ignored.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from lanereach.integrations.meetings.models import MeetingStatus

CALENDLY_TOLERANCE = timedelta(minutes=5)


class WebhookRejectedError(Exception):
    """The request is not a valid, signed booking webhook."""


@dataclass(frozen=True, slots=True)
class BookingNotice:
    """A booking change, whichever service sent it."""

    external_id: str
    status: MeetingStatus
    title: str
    starts_at: datetime
    ends_at: datetime | None
    invitee_email: str
    invitee_name: str | None
    organizer_email: str | None
    join_url: str | None


def verify_calendly(body: bytes, header: str | None, secret: str, now: datetime) -> None:
    """Check a Calendly signature.

    Args:
        body: Raw request body.
        header: `Calendly-Webhook-Signature`.
        secret: The webhook signing key.
        now: Current time.

    Raises:
        WebhookRejectedError: If the signature is missing, wrong or too old.
    """
    if not header:
        raise WebhookRejectedError("missing Calendly-Webhook-Signature")
    parts = dict(item.split("=", 1) for item in header.split(",") if "=" in item)
    try:
        timestamp = int(parts["t"])
        signature = parts["v1"]
    except (KeyError, ValueError) as exc:
        raise WebhookRejectedError("malformed Calendly-Webhook-Signature") from exc
    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise WebhookRejectedError("the signature does not match")
    if abs(now.timestamp() - timestamp) > CALENDLY_TOLERANCE.total_seconds():
        raise WebhookRejectedError("the signature is too old")


def verify_cal_com(body: bytes, header: str | None, secret: str) -> None:
    """Check a Cal.com signature.

    Args:
        body: Raw request body.
        header: `X-Cal-Signature-256`.
        secret: The webhook secret.

    Raises:
        WebhookRejectedError: If the signature is missing or wrong.
    """
    if not header:
        raise WebhookRejectedError("missing X-Cal-Signature-256")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, header.strip()):
        raise WebhookRejectedError("the signature does not match")


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def _text(value: Any, limit: int) -> str | None:
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else None


def _load(body: bytes) -> dict[str, Any]:
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise WebhookRejectedError("the body is not JSON") from exc
    if not isinstance(data, dict):
        raise WebhookRejectedError("the body is not a JSON object")
    return data


def read_calendly(body: bytes) -> BookingNotice | None:
    """Read an `invitee.created` / `invitee.canceled` webhook.

    Args:
        body: Raw (verified) body.

    Returns:
        The booking, or None for other events.

    Raises:
        WebhookRejectedError: If required fields are missing.
    """
    data = _load(body)
    event = data.get("event")
    if event not in ("invitee.created", "invitee.canceled"):
        return None
    payload = data.get("payload") or {}
    scheduled = payload.get("scheduled_event") or {}
    location = scheduled.get("location") or {}
    memberships = scheduled.get("event_memberships") or [{}]
    starts_at = _time(scheduled.get("start_time"))
    email = _text(payload.get("email"), 320)
    external_id = _text(scheduled.get("uri"), 300) or _text(payload.get("uri"), 300)
    if not (starts_at and email and external_id):
        raise WebhookRejectedError("the booking has no start, invitee email or id")
    if event == "invitee.canceled":
        status = MeetingStatus.CANCELED
    elif payload.get("old_invitee"):
        status = MeetingStatus.RESCHEDULED
    else:
        status = MeetingStatus.BOOKED
    return BookingNotice(
        external_id=external_id,
        status=status,
        title=_text(scheduled.get("name"), 300) or "Meeting",
        starts_at=starts_at,
        ends_at=_time(scheduled.get("end_time")),
        invitee_email=email.lower(),
        invitee_name=_text(payload.get("name"), 200),
        organizer_email=_text((memberships[0] or {}).get("user_email"), 320),
        join_url=_text(location.get("join_url"), 1000),
    )


def read_cal_com(body: bytes) -> BookingNotice | None:
    """Read a `BOOKING_CREATED` / `BOOKING_RESCHEDULED` / `BOOKING_CANCELLED` webhook.

    Args:
        body: Raw (verified) body.

    Returns:
        The booking, or None for other events.

    Raises:
        WebhookRejectedError: If required fields are missing.
    """
    data = _load(body)
    statuses = {
        "BOOKING_CREATED": MeetingStatus.BOOKED,
        "BOOKING_RESCHEDULED": MeetingStatus.RESCHEDULED,
        "BOOKING_CANCELLED": MeetingStatus.CANCELED,
    }
    status = statuses.get(str(data.get("triggerEvent")))
    if status is None:
        return None
    payload = data.get("payload") or {}
    attendees = payload.get("attendees") or [{}]
    attendee = attendees[0] if isinstance(attendees[0], dict) else {}
    starts_at = _time(payload.get("startTime"))
    email = _text(attendee.get("email"), 320)
    # A reschedule creates a new booking uid; the old one is `rescheduleUid`.
    external_id = (
        _text(payload.get("rescheduleUid"), 300) if status is MeetingStatus.RESCHEDULED else None
    )
    external_id = external_id or _text(payload.get("uid"), 300)
    if not (starts_at and email and external_id):
        raise WebhookRejectedError("the booking has no start, attendee email or uid")
    metadata = payload.get("metadata") or {}
    video = payload.get("videoCallData") or {}
    return BookingNotice(
        external_id=external_id,
        status=status,
        title=_text(payload.get("title"), 300) or "Meeting",
        starts_at=starts_at,
        ends_at=_time(payload.get("endTime")),
        invitee_email=email.lower(),
        invitee_name=_text(attendee.get("name"), 200),
        organizer_email=_text((payload.get("organizer") or {}).get("email"), 320),
        join_url=_text(metadata.get("videoCallUrl"), 1000) or _text(video.get("url"), 1000),
    )
