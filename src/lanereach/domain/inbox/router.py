"""Unified inbox endpoints: conversations of every agent mailbox and replying in place.

Threads are addressed with the `thread_id` query parameter because provider thread ids can
contain `/`, `+` and `=`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from pydantic import EmailStr, Field

from lanereach.auth.dependencies import IdempotencyGuardDependency, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.automation.engine.dependencies import EngineServicesDependency
from lanereach.core.idempotency import run_idempotent
from lanereach.core.openapi import READ, WRITE, problem_responses
from lanereach.core.schemas import ResponseModel, StrictRequestModel
from lanereach.domain.contacts.schemas import MessageResponse
from lanereach.domain.inbox.service import (
    MAX_LABEL_LENGTH,
    MAX_LABELS,
    ConversationSummary,
    Folder,
    InboxFilters,
    InboxService,
)

router = APIRouter(prefix="/inbox", tags=["Inbox"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]
# Answering contacts is what reviewers do with AI drafts; the same people answer by hand.
Responder = Annotated[Principal, Depends(require_scopes(Scope.AI_REVIEW))]
ThreadId = Annotated[str, Query(min_length=1, max_length=300, description="The email thread.")]

_REPLY = WRITE


class ConversationResponse(ResponseModel):
    """One conversation of the inbox."""

    thread_id: str
    subject: str
    preview: str
    contact_id: uuid.UUID | None
    contact_name: str
    contact_email: str
    company_name: str | None
    country: str | None
    office_id: uuid.UUID | None
    agent_id: uuid.UUID | None
    agent_name: str | None
    service_line: str | None
    last_message_at: datetime
    last_inbound_at: datetime | None
    last_outbound_at: datetime | None
    message_count: int
    inbound_count: int
    last_bucket: str | None
    last_direction: str
    unread: bool
    awaiting_reply: bool
    done: bool
    snoozed_until: datetime | None
    assignee_email: str | None
    pending_draft_id: uuid.UUID | None
    labels: list[str]


class ConversationPageResponse(ResponseModel):
    """A page of conversations."""

    items: list[ConversationResponse]
    next_cursor: str | None


class FolderCountsResponse(ResponseModel):
    """Conversations per folder."""

    inbox: int
    unread: int
    awaiting: int
    drafts: int
    snoozed: int
    done: int


class InboxMessageResponse(MessageResponse):
    """A message of a conversation, with the agent who sent it and its engagement."""

    sender_id: uuid.UUID | None
    tracking_id: uuid.UUID | None = None
    first_opened_at: datetime | None = None
    first_clicked_at: datetime | None = None
    open_count: int = 0
    click_count: int = 0


class ConversationDetailResponse(ResponseModel):
    """A conversation with its messages, oldest first."""

    conversation: ConversationResponse
    messages: list[InboxMessageResponse]


class ConversationUpdateRequest(StrictRequestModel):
    """What to change; fields left out stay as they are."""

    model_config = {
        "json_schema_extra": {
            "examples": [{"read": True}, {"snoozed_until": "2026-10-02T09:00:00-04:00"}]
        }
    }

    read: bool | None = None
    done: bool | None = None
    snoozed_until: datetime | None = None
    unsnooze: bool = False
    assignee_email: EmailStr | None = None
    unassign: bool = False
    labels: list[Annotated[str, Field(max_length=MAX_LABEL_LENGTH)]] | None = Field(
        default=None, max_length=MAX_LABELS
    )


class ReplyRequest(StrictRequestModel):
    """A reply written by a person; the agent's signature is added."""

    body: str = Field(min_length=1, max_length=20_000)


class ReplyResponse(ResponseModel):
    """The reply that was sent."""

    message: InboxMessageResponse
    conversation: ConversationResponse


def get_inbox_service(services: EngineServicesDependency) -> InboxService:
    """Inbox service for the request."""
    return InboxService(services.session, services.clock)


Inbox = Annotated[InboxService, Depends(get_inbox_service)]


def _conversation(summary: ConversationSummary) -> ConversationResponse:
    return ConversationResponse.model_validate(summary)


def _filters(
    principal: Principal,
    folder: Folder,
    sender_id: uuid.UUID | None,
    office_id: uuid.UUID | None,
    bucket: str | None,
    label: str | None,
    mine: bool,
    assignee_email: str | None,
    search: str | None,
) -> InboxFilters:
    return InboxFilters(
        folder=folder,
        sender_id=sender_id,
        office_id=office_id,
        bucket=bucket,
        label=label,
        assignee_email=principal.subject if mine else assignee_email,
        search=search,
    )


class _FilterQuery:
    """Query parameters shared by the list and the counts."""

    def __init__(
        self,
        sender_id: Annotated[uuid.UUID | None, Query(description="Agent mailbox.")] = None,
        office_id: Annotated[uuid.UUID | None, Query()] = None,
        bucket: Annotated[
            str | None, Query(max_length=40, description="Reply type, e.g. `positive`.")
        ] = None,
        label: Annotated[str | None, Query(max_length=MAX_LABEL_LENGTH)] = None,
        mine: Annotated[bool, Query(description="Only conversations assigned to me.")] = False,
        assignee_email: Annotated[str | None, Query(max_length=320)] = None,
        search: Annotated[
            str | None, Query(max_length=200, description="Contact name, email or subject.")
        ] = None,
    ) -> None:
        self.values: dict[str, Any] = {
            "sender_id": sender_id,
            "office_id": office_id,
            "bucket": bucket,
            "label": label,
            "mine": mine,
            "assignee_email": assignee_email,
            "search": search,
        }


FilterQuery = Annotated[_FilterQuery, Depends()]


@router.get(
    "/conversations",
    response_model=ConversationPageResponse,
    summary="Conversations of every agent mailbox",
    description="Newest activity first. Folders: `inbox` (the contact replied, not done or "
    "snoozed), `unread`, `awaiting` (the contact wrote last), `drafts` (an AI reply waits in "
    "the review queue), `snoozed`, `done`, `sent` (no reply yet) and `all`.",
    responses=READ,
)
async def list_conversations(
    principal: Reader,
    inbox: Inbox,
    filters: FilterQuery,
    folder: Annotated[Folder, Query()] = Folder.INBOX,
    cursor: Annotated[str | None, Query(max_length=400)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> ConversationPageResponse:
    """List conversations."""
    page = await inbox.list_conversations(
        _filters(principal, folder, **filters.values), limit=limit, cursor=cursor
    )
    return ConversationPageResponse(
        items=[_conversation(item) for item in page.items], next_cursor=page.next_cursor
    )


@router.get(
    "/counts",
    response_model=FolderCountsResponse,
    summary="Conversations per folder",
    responses=READ,
)
async def count_inbox_folders(
    principal: Reader, inbox: Inbox, filters: FilterQuery
) -> FolderCountsResponse:
    """Count conversations per folder with the same filters as the list."""
    counts = await inbox.count_folders(_filters(principal, Folder.ALL, **filters.values))
    return FolderCountsResponse.model_validate(counts)


@router.get("/labels", response_model=list[str], summary="Labels in use", responses=READ)
async def list_inbox_labels(_principal: Reader, inbox: Inbox) -> list[str]:
    """Labels used on conversations."""
    return await inbox.list_labels()


@router.get(
    "/conversation",
    response_model=ConversationDetailResponse,
    summary="A conversation with its messages",
    responses=problem_responses(401, 403, 404, 422),
)
async def get_conversation(
    thread_id: ThreadId, _principal: Reader, inbox: Inbox
) -> ConversationDetailResponse:
    """A conversation and its messages, oldest first."""
    summary, messages = await inbox.get_conversation(thread_id)
    return ConversationDetailResponse(
        conversation=_conversation(summary),
        messages=[InboxMessageResponse.model_validate(message) for message in messages],
    )


@router.patch(
    "/conversation",
    response_model=ConversationResponse,
    summary="Mark read/unread, done/reopen, snooze, assign or label",
    description="Done lasts until the contact writes again; snoozing hides the conversation "
    "until the given time.",
    responses=problem_responses(401, 403, 404, 422),
)
async def update_conversation(
    thread_id: ThreadId,
    payload: ConversationUpdateRequest,
    _principal: Responder,
    inbox: Inbox,
) -> ConversationResponse:
    """Change what people track about a conversation."""
    await inbox.update_conversation(
        thread_id,
        read=payload.read,
        done=payload.done,
        snoozed_until=payload.snoozed_until,
        unsnooze=payload.unsnooze,
        assignee_email=str(payload.assignee_email) if payload.assignee_email else None,
        unassign=payload.unassign,
        labels=payload.labels,
    )
    summary, _ = await inbox.get_conversation(thread_id)
    return _conversation(summary)


@router.post(
    "/conversation/reply",
    response_model=ReplyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Reply in the thread",
    description="Sent from the agent who owns the thread (another agent of the office when it "
    "is at its cap), with the agent's signature, through every send guardrail: a blocked reply "
    "returns 422, a deferred one (kill switch, sending window, mailbox capacity) returns 409.",
    responses=_REPLY,
)
async def reply_to_conversation(
    thread_id: ThreadId,
    payload: ReplyRequest,
    _principal: Responder,
    inbox: Inbox,
    services: EngineServicesDependency,
    idempotency: IdempotencyGuardDependency,
) -> ReplyResponse | JSONResponse:
    """Send a reply written in the inbox."""

    async def send() -> ReplyResponse:
        message = await inbox.send_reply(
            services, thread_id, body=payload.body, request_key=str(uuid.uuid4())
        )
        summary, _ = await inbox.get_conversation(thread_id)
        return ReplyResponse(
            message=InboxMessageResponse.model_validate(message),
            conversation=_conversation(summary),
        )

    return await run_idempotent(idempotency, status.HTTP_201_CREATED, send)
