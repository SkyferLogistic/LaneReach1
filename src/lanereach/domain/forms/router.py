"""Web form endpoints: managing forms (console) and the public submission endpoint."""

from __future__ import annotations

import html
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import Field

from lanereach.auth.dependencies import CurrentPrincipal, TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.core.dependencies import ContainerDependency
from lanereach.core.errors import NotFoundError
from lanereach.core.openapi import READ, WRITE, problem_responses
from lanereach.core.schemas import ResponseModel, StrictRequestModel
from lanereach.core.tenancy import open_tenant_session
from lanereach.domain.forms.service import (
    FORM_FIELDS,
    FormInput,
    FormService,
    find_form_tenant,
    submit_form,
)

router = APIRouter(tags=["Forms"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]
Writer = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_WRITE))]
MAX_FORM_BODY = 32_000


class FormRequest(StrictRequestModel):
    """A web form's settings."""

    name: str = Field(min_length=1, max_length=100, examples=["Request a quote"])
    office_id: uuid.UUID | None = Field(default=None, description="Office new contacts belong to.")
    tag: str | None = Field(default=None, max_length=64, examples=["website-quote"])
    fields: list[str] = Field(
        default_factory=lambda: [
            "first_name",
            "last_name",
            "company",
            "origin",
            "destination",
            "mode",
            "message",
        ],
        max_length=len(FORM_FIELDS),
        description="Asked in this order after the email: " + ", ".join(FORM_FIELDS),
    )
    redirect_url: str | None = Field(
        default=None, max_length=1000, description="https:// thank-you page."
    )
    thank_you_text: str = Field(
        default="Thank you, our team will come back to you shortly.", max_length=1000
    )
    is_active: bool = True


class FormResponse(ResponseModel):
    """A web form and how to embed it."""

    id: uuid.UUID
    name: str
    public_key: str
    office_id: uuid.UUID | None
    tag: str | None
    fields: list[str]
    redirect_url: str | None
    thank_you_text: str
    is_active: bool
    submission_count: int
    last_submission_at: datetime | None
    created_at: datetime
    action_url: str
    embed_html: str


class FormSubmissionResponse(ResponseModel):
    """One submission."""

    id: uuid.UUID
    contact_id: uuid.UUID | None
    email: str
    data: dict[str, Any]
    created_at: datetime


class FormFieldResponse(ResponseModel):
    """A field forms can ask for."""

    key: str
    label: str


def embed_snippet(action_url: str, fields: list[str]) -> str:
    """Copy-paste HTML for the website (plain form post, no script needed).

    Args:
        action_url: The public submission URL.
        fields: Fields in order.

    Returns:
        The HTML.
    """
    rows = ['  <label>Email <input type="email" name="email" required></label>']
    for key in fields:
        label = html.escape(FORM_FIELDS[key])
        if key == "message":
            rows.append(f'  <label>{label} <textarea name="message" rows="4"></textarea></label>')
        else:
            rows.append(f'  <label>{label} <input type="text" name="{key}"></label>')
    rows.append(
        '  <input type="text" name="website" tabindex="-1" autocomplete="off" '
        'style="position:absolute;left:-9999px" aria-hidden="true">'
    )
    rows.append('  <button type="submit">Send</button>')
    return (
        f'<form method="post" action="{html.escape(action_url)}">\n' + "\n".join(rows) + "\n</form>"
    )


def _describe(form: Any, public_base_url: str) -> FormResponse:
    action = f"{public_base_url.rstrip('/')}/api/v1/public/forms/{form.public_key}"
    return FormResponse(
        id=form.id,
        name=form.name,
        public_key=form.public_key,
        office_id=form.office_id,
        tag=form.tag,
        fields=list(form.fields),
        redirect_url=form.redirect_url,
        thank_you_text=form.thank_you_text,
        is_active=form.is_active,
        submission_count=form.submission_count,
        last_submission_at=form.last_submission_at,
        created_at=form.created_at,
        action_url=action,
        embed_html=embed_snippet(action, list(form.fields)),
    )


def _input(payload: FormRequest) -> FormInput:
    return FormInput(
        name=payload.name,
        office_id=payload.office_id,
        tag=payload.tag.strip().lower() if payload.tag else None,
        fields=payload.fields,
        redirect_url=payload.redirect_url,
        thank_you_text=payload.thank_you_text,
        is_active=payload.is_active,
    )


def get_form_service(session: TenantSession, container: ContainerDependency) -> FormService:
    """Form service for the request."""
    return FormService(session, container.clock)


Forms = Annotated[FormService, Depends(get_form_service)]


@router.get(
    "/forms/fields",
    response_model=list[FormFieldResponse],
    summary="Fields a form can ask for",
    responses=READ,
)
async def list_form_fields(_principal: Reader) -> list[FormFieldResponse]:
    """The field catalogue."""
    return [FormFieldResponse(key=key, label=label) for key, label in FORM_FIELDS.items()]


@router.get("/forms", response_model=list[FormResponse], summary="Web forms", responses=READ)
async def list_forms(
    _principal: Reader, forms: Forms, container: ContainerDependency
) -> list[FormResponse]:
    """Forms with their embed snippets."""
    return [
        _describe(form, container.settings.public_base_url) for form in await forms.list_forms()
    ]


@router.post(
    "/forms",
    response_model=FormResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a web form",
    responses=WRITE,
)
async def create_form(
    payload: FormRequest, principal: Writer, forms: Forms, container: ContainerDependency
) -> FormResponse:
    """Create a form."""
    form = await forms.create_form(_input(payload), principal.subject)
    return _describe(form, container.settings.public_base_url)


@router.put(
    "/forms/{form_id}", response_model=FormResponse, summary="Change a web form", responses=WRITE
)
async def update_form(
    form_id: uuid.UUID,
    payload: FormRequest,
    _principal: Writer,
    forms: Forms,
    container: ContainerDependency,
) -> FormResponse:
    """Change a form (its public key stays, so the website keeps working)."""
    return _describe(
        await forms.update_form(form_id, _input(payload)), container.settings.public_base_url
    )


@router.delete(
    "/forms/{form_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a web form",
    responses=WRITE,
)
async def delete_form(form_id: uuid.UUID, _principal: Writer, forms: Forms) -> Response:
    """Delete a form and its submissions."""
    await forms.delete_form(form_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/forms/{form_id}/submissions",
    response_model=list[FormSubmissionResponse],
    summary="Recent submissions of a form",
    responses=READ,
)
async def list_form_submissions(
    form_id: uuid.UUID, _principal: Reader, _caller: CurrentPrincipal, forms: Forms
) -> list[FormSubmissionResponse]:
    """Submissions, newest first."""
    return [
        FormSubmissionResponse.model_validate(row) for row in await forms.list_submissions(form_id)
    ]


def _thank_you_page(text: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Thank you</title><style>body{font-family:system-ui,sans-serif;display:grid;"
        "place-items:center;min-height:90vh;margin:0;color:#0f1b2d}"
        "main{max-width:480px;padding:24px;text-align:center}</style></head>"
        f"<body><main><h1>Thank you</h1><p>{html.escape(text)}</p></main></body></html>"
    )


@router.post(
    "/public/forms/{public_key}",
    summary="Submit a web form (public)",
    description="Accepts a normal HTML form post (`application/x-www-form-urlencoded` or "
    "`multipart/form-data`, answered with the thank-you page or a redirect) or JSON (answered "
    "with JSON, CORS open). `email` is required; the hidden `website` field must stay empty.",
    responses={
        200: {"description": "Received."},
        303: {"description": "Redirect to the thank-you page."},
        **problem_responses(404, 413, 422, 429),
    },
)
async def submit_public_form(
    public_key: str, request: Request, container: ContainerDependency
) -> Response:
    """Receive a submission."""
    content_type = request.headers.get("content-type", "")
    if int(request.headers.get("content-length") or 0) > MAX_FORM_BODY:
        return JSONResponse({"detail": "Too large."}, status_code=413)
    is_json = content_type.startswith("application/json")
    if is_json:
        body = await request.json()
        raw: dict[str, Any] = body if isinstance(body, dict) else {}
    else:
        form_data = await request.form()
        raw = {key: value for key, value in form_data.items() if isinstance(value, str)}
    tenant_id = await find_form_tenant(container.session_factory, public_key)
    if tenant_id is None:
        raise NotFoundError("This form is not available.")
    settings = container.settings
    async with open_tenant_session(container.session_factory, tenant_id) as session:
        outcome = await submit_form(
            session,
            container.clock,
            public_key,
            raw,
            task_queue=container.task_queue,
            per_email_per_hour=settings.form_submissions_per_email_per_hour,
            per_form_per_minute=settings.form_submissions_per_form_per_minute,
        )
        redirect_url, thank_you = outcome.form.redirect_url, outcome.form.thank_you_text
    if is_json:
        return JSONResponse(
            {"ok": True, "message": thank_you}, headers={"Access-Control-Allow-Origin": "*"}
        )
    if redirect_url:
        return RedirectResponse(redirect_url, status_code=status.HTTP_303_SEE_OTHER)
    return HTMLResponse(_thank_you_page(thank_you))
