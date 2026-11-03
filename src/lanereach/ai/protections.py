"""Protection actions applied after a reply is classified (and on provider bounce/complaint events).

Every action here only reduces email: stopping sequences, suppressing, freezing, pausing. They
are deterministic and idempotent (re-processing a message never stacks duplicate effects).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from sqlalchemy import func, select

from lanereach.ai.policy import ProtectionAction
from lanereach.ai.schemas import ReplyBucket
from lanereach.automation.engine.services import EngineServices
from lanereach.automation.enrollments.models import (
    OPEN_ENROLLMENT_STATUSES,
    Enrollment,
    EnrollmentStatus,
)
from lanereach.automation.enrollments.service import enroll_contact_in_workflow, stop_enrollment
from lanereach.automation.workflows.graph import WorkflowGraph
from lanereach.automation.workflows.models import Workflow, WorkflowVersion
from lanereach.domain.companies.models import Company
from lanereach.domain.contacts.models import Contact, ContactStage
from lanereach.domain.offices.models import Office
from lanereach.domain.senders.models import Sender
from lanereach.domain.suppressions.models import Suppression, SuppressionReason
from lanereach.integrations.notifications.notifier import NotificationMessage
from lanereach.tenants.models import Tenant

logger = structlog.get_logger(__name__)

REPLY_TRIGGER = "trigger_reply_classified"

_DNC_REASON = {
    ReplyBucket.UNSUBSCRIBE: SuppressionReason.UNSUBSCRIBED,
    ReplyBucket.COMPLAINT: SuppressionReason.COMPLAINT,
}
_SUPPRESS_REASON = {
    ReplyBucket.BOUNCE_HARD: SuppressionReason.BOUNCED_HARD,
    ReplyBucket.BLOCKED: SuppressionReason.BLOCKED,
}


@dataclass
class ProtectionContext:
    """Rows the protections act on."""

    contact: Contact
    company: Company | None
    office: Office | None
    sender: Sender | None
    bucket: ReplyBucket
    summary: str
    return_date: date | None = None
    stopped_workflow_ids: list[uuid.UUID] = field(default_factory=list)
    actions_taken: list[str] = field(default_factory=list)


class ReplyProtections:
    """Applies `ProtectionAction`s.

    Args:
        services: Engine services (session, clock, settings, notifier, calendars).
    """

    def __init__(self, services: EngineServices) -> None:
        self._services = services
        self._session = services.session

    async def apply(self, action: ProtectionAction, context: ProtectionContext) -> None:
        """Apply one action.

        Args:
            action: The action.
            context: Rows and classification.
        """
        handler = {
            ProtectionAction.STOP_SEQUENCE: self._stop_outreach,
            ProtectionAction.REENROLL_LATER: self._reenroll_later,
            ProtectionAction.REST: self._rest,
            ProtectionAction.DO_NOT_CONTACT: self._do_not_contact,
            ProtectionAction.FREEZE_COMPANY: self._freeze_company,
            ProtectionAction.ALERT_OWNER: self._alert_owner,
            ProtectionAction.SUPPRESS_CONTACT: self._suppress,
            ProtectionAction.FLAG_MAILBOX: self._flag_mailbox,
            ProtectionAction.SOFT_BOUNCE_RETRY: self._soft_bounce,
            ProtectionAction.RESCHEDULE: self._reschedule_after_absence,
        }[action]
        await handler(context)

    async def _open_outreach(self, contact: Contact) -> list[Enrollment]:
        """Open enrollments of the contact, except reply-handling workflows."""
        rows = (
            await self._session.execute(
                select(Enrollment, WorkflowVersion.graph)
                .join(
                    WorkflowVersion,
                    (WorkflowVersion.workflow_id == Enrollment.workflow_id)
                    & (WorkflowVersion.version == Enrollment.workflow_version),
                )
                .where(
                    Enrollment.contact_id == contact.id,
                    Enrollment.status.in_(OPEN_ENROLLMENT_STATUSES),
                )
                .with_for_update(of=Enrollment)
            )
        ).all()
        return [
            enrollment
            for enrollment, graph in rows
            if WorkflowGraph.model_validate(graph).trigger.type != REPLY_TRIGGER
        ]

    async def _stop_outreach(self, context: ProtectionContext) -> None:
        stopped = 0
        for enrollment in await self._open_outreach(context.contact):
            stop_enrollment(enrollment, f"contact replied ({context.bucket})", self._services.clock)
            context.stopped_workflow_ids.append(enrollment.workflow_id)
            stopped += 1
        if stopped:
            context.actions_taken.append(f"stopped {stopped} outreach enrollment(s)")

    async def _add_suppression(
        self,
        contact: Contact,
        reason: SuppressionReason,
        *,
        expires_at: datetime | None,
        source: str,
    ) -> bool:
        existing = await self._session.scalar(
            select(func.count())
            .select_from(Suppression)
            .where(
                func.lower(Suppression.email) == contact.email.lower(),
                Suppression.reason == reason,
                Suppression.expires_at.is_(None)
                if expires_at is None
                else Suppression.expires_at >= expires_at,
            )
        )
        if existing:
            return False
        self._session.add(
            Suppression(
                tenant_id=contact.tenant_id,
                contact_id=contact.id,
                email=contact.email.lower(),
                reason=reason,
                source=source,
                expires_at=expires_at,
                created_at=self._services.clock.now(),
            )
        )
        await self._session.flush()
        return True

    async def _do_not_contact(self, context: ProtectionContext) -> None:
        reason = _DNC_REASON.get(context.bucket, SuppressionReason.DO_NOT_CONTACT)
        await self._add_suppression(context.contact, reason, expires_at=None, source="reply")
        context.contact.stage = ContactStage.DO_NOT_CONTACT
        context.contact.updated_at = self._services.clock.now()
        context.actions_taken.append(f"do not contact ({reason})")

    async def _suppress(self, context: ProtectionContext) -> None:
        reason = _SUPPRESS_REASON.get(context.bucket, SuppressionReason.BOUNCED_HARD)
        await self._add_suppression(context.contact, reason, expires_at=None, source="bounce")
        context.actions_taken.append(f"suppressed ({reason})")

    async def _freeze_company(self, context: ProtectionContext) -> None:
        company = context.company
        if company is None:
            return
        if company.frozen_at is None:
            company.frozen_at = self._services.clock.now()
            company.frozen_reason = f"{context.bucket} from {context.contact.email}"[:200]
            company.updated_at = company.frozen_at
        context.actions_taken.append(f"froze company '{company.name}'")

    async def _alert_owner(self, context: ProtectionContext) -> None:
        if context.office is None:
            return
        contact = context.contact
        await self._services.notifier.notify(
            NotificationMessage(
                channel="office_owner",
                recipient=context.office.owner_email,
                subject=f"{context.bucket.replace('_', ' ').title()}: {contact.display_name}",
                body=f"{contact.display_name} ({contact.email}) replied: {context.summary}",
                payload={"contact_id": str(context.contact.id), "bucket": context.bucket.value},
                contact_id=context.contact.id,
            )
        )
        context.actions_taken.append("alerted office owner")

    async def _flag_mailbox(self, context: ProtectionContext) -> None:
        sender = context.sender
        if sender is None or sender.flagged_at is not None:
            return
        sender.flagged_at = self._services.clock.now()
        sender.flag_reason = f"blocked by {context.contact.email_domain}"[:200]
        sender.updated_at = sender.flagged_at
        context.actions_taken.append(f"flagged mailbox {sender.email}")

    async def _soft_bounce(self, context: ProtectionContext) -> None:
        settings = self._services.settings
        now = self._services.clock.now()
        recent = await self._session.scalar(
            select(func.count())
            .select_from(Suppression)
            .where(
                func.lower(Suppression.email) == context.contact.email.lower(),
                Suppression.reason == SuppressionReason.BOUNCED_SOFT,
                Suppression.created_at >= now - timedelta(days=settings.soft_bounce_window_days),
            )
        )
        if (recent or 0) + 1 >= settings.soft_bounce_limit:
            await self._add_suppression(
                context.contact,
                SuppressionReason.BOUNCED_SOFT,
                expires_at=None,
                source="soft bounce limit",
            )
            await self._stop_outreach(context)
            context.actions_taken.append("suppressed after repeated soft bounces")
            return
        self._session.add(
            Suppression(
                tenant_id=context.contact.tenant_id,
                contact_id=context.contact.id,
                email=context.contact.email.lower(),
                reason=SuppressionReason.BOUNCED_SOFT,
                source="soft bounce",
                expires_at=now + timedelta(hours=settings.soft_bounce_retry_hours),
                created_at=now,
            )
        )
        await self._session.flush()
        context.actions_taken.append(f"retry after {settings.soft_bounce_retry_hours} h")

    async def _rest(self, context: ProtectionContext) -> None:
        until = self._services.clock.now() + timedelta(
            days=self._services.settings.not_interested_rest_days
        )
        await self._add_suppression(
            context.contact, SuppressionReason.MANUAL, expires_at=until, source="rest"
        )
        context.contact.stage = ContactStage.NOT_INTERESTED
        context.contact.updated_at = self._services.clock.now()
        context.actions_taken.append(f"resting until {until.date().isoformat()}")

    async def _local_datetime(self, contact: Contact, day: date) -> datetime:
        calendar = await self._services.calendars.get_calendar(contact.country)
        zone = calendar.timezone
        if contact.timezone:
            try:
                zone = ZoneInfo(contact.timezone)
            except (ZoneInfoNotFoundError, ValueError):
                zone = calendar.timezone
        tenant_start = await self._window_start(contact)
        return datetime.combine(day, tenant_start, tzinfo=zone)

    async def _window_start(self, contact: Contact) -> time:
        tenant = await self._session.get(Tenant, contact.tenant_id)
        assert tenant is not None  # noqa: S101 - the session's own tenant
        return tenant.sending_window_start

    async def _reenroll_later(self, context: ProtectionContext) -> None:
        wake_day = (
            self._services.clock.now() + timedelta(days=self._services.settings.later_reenroll_days)
        ).date()
        wake_at = await self._local_datetime(context.contact, context.return_date or wake_day)
        count = 0
        for workflow_id in dict.fromkeys(context.stopped_workflow_ids):
            workflow = await self._session.get(Workflow, workflow_id)
            if workflow is None:
                continue
            attempt = await enroll_contact_in_workflow(
                self._session,
                self._services.clock,
                workflow=workflow,
                contact=context.contact,
                enrolled_by="reply:later",
            )
            if attempt.enrollment is not None:
                attempt.enrollment.status = EnrollmentStatus.WAITING
                attempt.enrollment.next_run_at = wake_at
                count += 1
        context.actions_taken.append(
            f"re-enroll on {wake_at.date().isoformat()} ({count} workflow(s))"
        )

    async def _reschedule_after_absence(self, context: ProtectionContext) -> None:
        default_day = (
            self._services.clock.now()
            + timedelta(days=self._services.settings.out_of_office_default_days)
        ).date()
        wake_at = await self._local_datetime(context.contact, context.return_date or default_day)
        moved = 0
        for enrollment in await self._open_outreach(context.contact):
            if enrollment.status is EnrollmentStatus.PAUSED:
                continue
            current = enrollment.next_run_at or self._services.clock.now()
            if current < wake_at:
                enrollment.next_run_at = wake_at
                enrollment.status = EnrollmentStatus.WAITING
                enrollment.updated_at = self._services.clock.now()
                moved += 1
        context.actions_taken.append(
            f"rescheduled {moved} enrollment(s) to {wake_at.date().isoformat()}"
        )
