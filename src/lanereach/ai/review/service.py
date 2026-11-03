"""Review queue: create, list, approve (and send), edit-and-approve, reject."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

import structlog
from sqlalchemy import select

from lanereach.ai.review.models import ReviewItem, ReviewKind, ReviewStatus
from lanereach.auth.principal import Principal
from lanereach.automation.engine.email import choose_sender, deliver_email, thread_reference
from lanereach.automation.engine.services import EngineServices
from lanereach.automation.enrollments.models import Enrollment, EnrollmentStatus
from lanereach.automation.guardrails.base import EmailKind, GuardrailOutcome
from lanereach.core.errors import BusinessRuleViolationError, ConflictError, NotFoundError
from lanereach.core.repository import TenantRepository
from lanereach.core.tenancy import require_session_tenant_id, run_after_commit
from lanereach.domain.companies.models import Company
from lanereach.domain.contacts.models import Contact
from lanereach.domain.messages.models import Message, MessageDirection
from lanereach.domain.offices.models import Office
from lanereach.domain.senders.models import Sender
from lanereach.domain.templates.models import Template
from lanereach.tenants.models import Tenant

logger = structlog.get_logger(__name__)


async def create_review_item(
    services: EngineServices,
    *,
    kind: ReviewKind,
    payload: dict[str, Any],
    reason: str | None,
    contact_id: uuid.UUID | None = None,
    message_id: uuid.UUID | None = None,
    office_id: uuid.UUID | None = None,
    enrollment_id: uuid.UUID | None = None,
    ai_run_id: uuid.UUID | None = None,
) -> ReviewItem:
    """Queue AI output for a person.

    Args:
        services: Engine services.
        kind: What is reviewed.
        payload: Draft, classification or localization plus context.
        reason: Why a human is needed (shown in the queue).
        contact_id: Contact concerned.
        message_id: Inbound message concerned.
        office_id: Office that would send a reply.
        enrollment_id: Enrollment that asked for it.
        ai_run_id: AI run that produced it.

    Returns:
        The pending item.
    """
    now = services.clock.now()
    item = ReviewItem(
        tenant_id=require_session_tenant_id(services.session),
        kind=kind,
        status=ReviewStatus.PENDING,
        payload=payload,
        reason=reason,
        contact_id=contact_id,
        message_id=message_id,
        office_id=office_id,
        enrollment_id=enrollment_id,
        ai_run_id=ai_run_id,
        created_at=now,
        updated_at=now,
    )
    services.session.add(item)
    await services.session.flush()
    logger.info("review_item_created", review_item_id=str(item.id), kind=kind.value)
    return item


class ReviewRepository(TenantRepository[ReviewItem]):
    """Queries over review items."""

    model = ReviewItem

    async def list_items(
        self,
        *,
        status: ReviewStatus | None,
        kind: ReviewKind | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[Sequence[ReviewItem], str | None]:
        """Items newest first."""
        statement = select(ReviewItem)
        if status is not None:
            statement = statement.where(ReviewItem.status == status)
        if kind is not None:
            statement = statement.where(ReviewItem.kind == kind)
        return await self.page(statement, limit=limit, cursor=cursor)


class ReviewService:
    """Decisions on review items.

    Args:
        services: Engine services bound to the request session.
    """

    def __init__(self, services: EngineServices) -> None:
        self._services = services
        self._session = services.session
        self.repository = ReviewRepository(services.session)

    async def require_item(self, item_id: uuid.UUID, *, for_update: bool = False) -> ReviewItem:
        """Load an item or raise 404."""
        item = await self.repository.get(item_id, for_update=for_update)
        if item is None:
            raise NotFoundError("Review item not found.")
        return item

    async def approve(
        self,
        item_id: uuid.UUID,
        principal: Principal,
        *,
        subject: str | None = None,
        body: str | None = None,
    ) -> ReviewItem:
        """Approve an item; reply drafts are sent in the original thread.

        Args:
            item_id: Item ID.
            principal: The reviewer.
            subject: Edited subject (edit-and-approve).
            body: Edited body (edit-and-approve).

        Returns:
            The decided item.

        Raises:
            NotFoundError: If missing.
            ConflictError: If already decided, or sending is deferred (kill switch, capacity):
                the item stays pending so it can be approved again later.
            BusinessRuleViolationError: If a guardrail blocks the reply (suppressed contact,
                frozen company).
        """
        item = await self.require_item(item_id, for_update=True)
        if item.status is not ReviewStatus.PENDING:
            raise ConflictError(f"This item was already {item.status}.")
        edited = subject is not None or body is not None
        payload = dict(item.payload)
        if edited:
            payload["original"] = {"subject": payload.get("subject"), "body": payload.get("body")}
            payload["subject"] = subject if subject is not None else payload.get("subject")
            payload["body"] = body if body is not None else payload.get("body")
        if item.kind is ReviewKind.REPLY_DRAFT:
            item.sent_message_id = await self._send_reply(
                item, str(payload["subject"]), str(payload["body"])
            )
        elif item.kind is ReviewKind.LOCALIZED_TEMPLATE:
            await self._save_localized_template(payload)
        now = self._services.clock.now()
        item.payload = payload
        item.status = ReviewStatus.EDITED if edited else ReviewStatus.APPROVED
        item.reviewer = principal.subject
        item.decided_at = now
        item.updated_at = now
        await self._mark_enrollment_draft(
            item, "sent" if item.kind is ReviewKind.REPLY_DRAFT else "approved"
        )
        return item

    async def reject(
        self, item_id: uuid.UUID, principal: Principal, note: str | None
    ) -> ReviewItem:
        """Reject an item (nothing is sent).

        Raises:
            NotFoundError: If missing.
            ConflictError: If already decided.
        """
        item = await self.require_item(item_id, for_update=True)
        if item.status is not ReviewStatus.PENDING:
            raise ConflictError(f"This item was already {item.status}.")
        now = self._services.clock.now()
        item.status = ReviewStatus.REJECTED
        item.reviewer = principal.subject
        item.decision_note = note
        item.decided_at = now
        item.updated_at = now
        await self._mark_enrollment_draft(item, "rejected")
        return item

    async def _send_reply(self, item: ReviewItem, subject: str, body: str) -> str:
        session = self._session
        contact = await session.get(Contact, item.contact_id) if item.contact_id else None
        inbound = await session.get(Message, item.message_id) if item.message_id else None
        if contact is None or inbound is None:
            raise BusinessRuleViolationError(
                "The reply's contact or original message no longer exists."
            )
        office = await session.get(Office, item.office_id) if item.office_id else None
        office = office or (
            await session.get(Office, contact.office_id) if contact.office_id else None
        )
        if office is None:
            raise BusinessRuleViolationError("No office can send this reply.")
        thread = await thread_reference(session, inbound.thread_id, inbound.subject)
        assert thread is not None  # noqa: S101 - the inbound message is in the thread
        last_sender_id = await session.scalar(
            select(Message.sender_id)
            .where(
                Message.thread_id == inbound.thread_id,
                Message.direction == MessageDirection.OUTBOUND,
            )
            .order_by(Message.occurred_at.desc())
            .limit(1)
        )
        sender = await choose_sender(session, office, self._services.clock.now(), last_sender_id)
        if sender is None:
            sender = await choose_sender(session, office, self._services.clock.now(), None)
        tenant = await session.get(Tenant, contact.tenant_id)
        company = await session.get(Company, contact.company_id) if contact.company_id else None
        assert tenant is not None  # noqa: S101 - own tenant
        result = await deliver_email(
            self._services,
            kind=EmailKind.APPROVED_REPLY,
            tenant=tenant,
            contact=contact,
            company=company,
            office=office,
            sender=sender if isinstance(sender, Sender) else None,
            subject=subject,
            body=body,
            idempotency_key=f"review:{item.id}",
            thread=thread,
            enrollment_id=item.enrollment_id,
        )
        verdict = result.verdict
        if verdict.outcome is GuardrailOutcome.BLOCK:
            raise BusinessRuleViolationError(
                f"Not sent: {verdict.deciding_guardrail} - {verdict.reason}"
            )
        if verdict.outcome is GuardrailOutcome.DEFER:
            until = verdict.defer_until.isoformat() if verdict.defer_until else "later"
            raise ConflictError(
                f"Not sent yet: {verdict.deciding_guardrail} - {verdict.reason}. "
                f"Approve again after {until}."
            )
        assert result.message is not None  # noqa: S101 - sent deliveries are recorded
        return result.message.message_id

    async def _save_localized_template(self, payload: dict[str, Any]) -> None:
        key, language = payload.get("template_key"), payload.get("language")
        if not key or not language:
            raise BusinessRuleViolationError("The localization has no template key or language.")
        now = self._services.clock.now()
        template = await self._session.scalar(
            select(Template).where(Template.key == key, Template.language == language)
        )
        if template is None:
            template = Template(
                tenant_id=require_session_tenant_id(self._session),
                key=key,
                language=language,
                name=f"{key} ({language})",
                created_at=now,
            )
            self._session.add(template)
        template.subject = str(payload["subject"])
        template.body = str(payload["body"])
        template.updated_at = now
        await self._session.flush()

    async def _mark_enrollment_draft(self, item: ReviewItem, status: str) -> None:
        if item.enrollment_id is None:
            return
        enrollment = await self._session.get(Enrollment, item.enrollment_id)
        if enrollment is None:
            return
        draft = dict(enrollment.variables.get("draft") or {})
        if draft.get("review_item_id") != str(item.id):
            return
        draft["status"] = status
        draft["subject"] = item.payload.get("subject")
        draft["body"] = item.payload.get("body")
        enrollment.variables = {**enrollment.variables, "draft": draft}
        if enrollment.status is EnrollmentStatus.WAITING:
            # A `reply_in_thread` node may be waiting for this decision: wake it now.
            now = self._services.clock.now()
            enrollment.next_run_at = now
            enrollment.updated_at = now
            queue = self._services.task_queue
            if queue is not None:
                tenant_id, enrollment_id = enrollment.tenant_id, enrollment.id

                async def wake() -> None:
                    await queue.enqueue_advance(tenant_id, enrollment_id)

                run_after_commit(self._session, wake)
