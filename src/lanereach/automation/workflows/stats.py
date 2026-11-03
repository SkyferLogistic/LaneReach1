"""Campaign results: what a workflow's enrollments did, from enrolment to deal.

Emails, opens and clicks come from the workflow's own messages. Replies, bounces, leads and
unsubscribes are counted per enrolled contact after they were enrolled, because inbound mail
does not carry the enrollment it answers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.automation.enrollments.models import Enrollment, EnrollmentStatus, EnrollmentStep
from lanereach.domain.crm.models import Deal
from lanereach.domain.leads.models import Lead
from lanereach.domain.messages.models import Message, MessageDirection, MessageKind
from lanereach.domain.suppressions.models import Suppression, SuppressionReason
from lanereach.integrations.tracking.models import EmailEvent, EmailEventKind
from lanereach.reporting.insights import POSITIVE_BUCKETS

TOP_LINKS = 10


@dataclass(frozen=True, slots=True)
class StepCount:
    """How many enrollments ran a node, and which branch they took."""

    node_id: str
    node_type: str
    enrollments: int
    handles: dict[str, int]


@dataclass(frozen=True, slots=True)
class LinkClicks:
    """Human clicks on one link."""

    url: str
    clicks: int
    contacts: int


@dataclass(slots=True)
class CampaignStats:
    """The funnel of one workflow."""

    workflow_id: uuid.UUID
    enrolled: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    sent: int = 0
    contacts_emailed: int = 0
    opened: int = 0
    clicked: int = 0
    tracked: int = 0
    replied: int = 0
    positive: int = 0
    bounced: int = 0
    unsubscribed: int = 0
    leads: int = 0
    deals: int = 0
    won: int = 0
    steps: list[StepCount] = field(default_factory=list)
    links: list[LinkClicks] = field(default_factory=list)

    @property
    def open_rate(self) -> float:
        """Unique opens per tracked email sent."""
        return self.opened / self.tracked if self.tracked else 0.0

    @property
    def click_rate(self) -> float:
        """Unique clicks per tracked email sent."""
        return self.clicked / self.tracked if self.tracked else 0.0

    @property
    def reply_rate(self) -> float:
        """Contacts who replied per contact emailed."""
        return self.replied / self.contacts_emailed if self.contacts_emailed else 0.0


def _enrolled(workflow_id: uuid.UUID) -> Select[uuid.UUID, datetime]:
    """Each enrolled contact with their first enrolment time."""
    return (
        select(Enrollment.contact_id, func.min(Enrollment.created_at).label("since"))
        .where(Enrollment.workflow_id == workflow_id)
        .group_by(Enrollment.contact_id)
    )


async def build_campaign_stats(session: AsyncSession, workflow_id: uuid.UUID) -> CampaignStats:
    """Count a workflow's funnel.

    Args:
        session: Tenant session.
        workflow_id: The workflow (its existence is checked by the caller).

    Returns:
        The numbers.
    """
    stats = CampaignStats(workflow_id)
    by_status = dict(
        (
            await session.execute(
                select(Enrollment.status, func.count())
                .where(Enrollment.workflow_id == workflow_id)
                .group_by(Enrollment.status)
            )
        ).all()
    )
    stats.by_status = {status.value: int(by_status.get(status, 0)) for status in EnrollmentStatus}
    stats.enrolled = sum(stats.by_status.values())

    enrollment_ids = select(Enrollment.id).where(Enrollment.workflow_id == workflow_id)
    ours = and_(
        Message.enrollment_id.in_(enrollment_ids), Message.direction == MessageDirection.OUTBOUND
    )
    sent = (
        await session.execute(
            select(
                func.count(),
                func.count(func.distinct(Message.contact_id)),
                func.count().filter(Message.tracking_id.is_not(None)),
                func.count().filter(Message.first_opened_at.is_not(None)),
                func.count().filter(Message.first_clicked_at.is_not(None)),
            ).where(ours)
        )
    ).one()
    stats.sent, stats.contacts_emailed, stats.tracked, stats.opened, stats.clicked = (
        int(value or 0) for value in sent
    )

    enrolled = _enrolled(workflow_id).subquery("enrolled")
    after = and_(
        Message.contact_id == enrolled.c.contact_id, Message.occurred_at >= enrolled.c.since
    )
    inbound = Message.direction == MessageDirection.INBOUND
    bucket = Message.processing_result["classification"]["bucket"].astext
    replies = (
        await session.execute(
            select(
                func.count(func.distinct(Message.contact_id)).filter(
                    Message.kind == MessageKind.REPLY
                ),
                func.count(func.distinct(Message.contact_id)).filter(
                    Message.kind == MessageKind.REPLY, bucket.in_(POSITIVE_BUCKETS)
                ),
                func.count(func.distinct(Message.contact_id)).filter(
                    Message.kind == MessageKind.BOUNCE
                ),
            )
            .select_from(Message)
            .join(enrolled, after)
            .where(inbound)
        )
    ).one()
    stats.replied, stats.positive, stats.bounced = (int(value or 0) for value in replies)
    stats.unsubscribed = int(
        await session.scalar(
            select(func.count(func.distinct(Suppression.contact_id)))
            .join(enrolled, Suppression.contact_id == enrolled.c.contact_id)
            .where(
                Suppression.reason == SuppressionReason.UNSUBSCRIBED,
                Suppression.created_at >= enrolled.c.since,
            )
        )
        or 0
    )
    leads = (
        await session.execute(
            select(func.count(func.distinct(Lead.id)), func.count(func.distinct(Deal.id)))
            .select_from(Lead)
            .join(enrolled, Lead.contact_id == enrolled.c.contact_id)
            .outerjoin(Deal, Deal.lead_id == Lead.id)
            .where(Lead.created_at >= enrolled.c.since)
        )
    ).one()
    stats.leads, stats.deals = (int(value or 0) for value in leads)
    stats.won = int(
        await session.scalar(
            select(func.count(func.distinct(Deal.id)))
            .join(Lead, Deal.lead_id == Lead.id)
            .join(enrolled, Lead.contact_id == enrolled.c.contact_id)
            .where(Lead.created_at >= enrolled.c.since, Deal.won_at.is_not(None))
        )
        or 0
    )
    stats.steps = await _step_counts(session, enrollment_ids)
    stats.links = await _top_links(session, ours)
    return stats


async def _step_counts(session: AsyncSession, enrollment_ids: object) -> list[StepCount]:
    rows = (
        await session.execute(
            select(
                EnrollmentStep.node_id,
                EnrollmentStep.node_type,
                EnrollmentStep.handle_taken,
                func.count(func.distinct(EnrollmentStep.enrollment_id)),
            )
            .where(EnrollmentStep.enrollment_id.in_(enrollment_ids))  # type: ignore[arg-type]
            .group_by(EnrollmentStep.node_id, EnrollmentStep.node_type, EnrollmentStep.handle_taken)
        )
    ).all()
    totals = (
        await session.execute(
            select(
                EnrollmentStep.node_id,
                EnrollmentStep.node_type,
                func.count(func.distinct(EnrollmentStep.enrollment_id)),
            )
            .where(EnrollmentStep.enrollment_id.in_(enrollment_ids))  # type: ignore[arg-type]
            .group_by(EnrollmentStep.node_id, EnrollmentStep.node_type)
        )
    ).all()
    handles: dict[str, dict[str, int]] = {}
    for node_id, _node_type, handle, count in rows:
        if handle:
            handles.setdefault(node_id, {})[handle] = int(count)
    return sorted(
        (
            StepCount(node_id, node_type, int(count), handles.get(node_id, {}))
            for node_id, node_type, count in totals
        ),
        key=lambda step: -step.enrollments,
    )


async def _top_links(session: AsyncSession, ours: object) -> list[LinkClicks]:
    rows = (
        await session.execute(
            select(
                EmailEvent.url,
                func.count(),
                func.count(func.distinct(Message.contact_id)),
            )
            .join(Message, Message.id == EmailEvent.message_id)
            .where(
                ours,  # type: ignore[arg-type]
                EmailEvent.kind == EmailEventKind.CLICKED,
                EmailEvent.suspected_bot.is_(False),
                EmailEvent.url.is_not(None),
            )
            .group_by(EmailEvent.url)
            .order_by(func.count().desc())
            .limit(TOP_LINKS)
        )
    ).all()
    return [LinkClicks(str(url), int(clicks), int(contacts)) for url, clicks, contacts in rows]
