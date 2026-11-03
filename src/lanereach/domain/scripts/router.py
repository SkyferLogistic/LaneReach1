"""Scripts endpoints: the script-by-language matrix, uploads, sync, translation, previews."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import Field

from lanereach.ai.router import AIRateLimit, get_ai_runtime
from lanereach.ai.tasks.runtime import AIRuntime
from lanereach.auth.dependencies import (
    CurrentPrincipal,
    IdempotencyGuardDependency,
    TenantSession,
    require_scopes,
)
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.core.dependencies import ContainerDependency
from lanereach.core.errors import NotFoundError
from lanereach.core.idempotency import run_idempotent
from lanereach.core.languages import LANGUAGE_VARIANT_PATTERN
from lanereach.core.openapi import READ, WRITE
from lanereach.core.schemas import ResponseModel, StrictRequestModel
from lanereach.domain.assets.models import AssetKind, EmailAsset
from lanereach.domain.assets.service import AssetService, asset_url
from lanereach.domain.contacts.models import Contact
from lanereach.domain.countries.service import destination_phrase
from lanereach.domain.scripts.service import (
    ScriptLibraryService,
    approve_ai_draft,
    build_script_matrix,
    preview_script,
)
from lanereach.domain.scripts.sync import open_script_library, sync_tenant_library
from lanereach.domain.senders.models import Sender, ServiceLine
from lanereach.domain.templates.models import Template, TemplateStatus
from lanereach.domain.templates.router import TemplateResponse
from lanereach.integrations.documents.service import get_library_connection
from lanereach.tenants.models import Tenant

router = APIRouter(prefix="/scripts", tags=["Scripts"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]
Writer = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_WRITE))]
Publisher = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_PUBLISH))]
_MISSING = re.compile(r"\[([a-z_]+\.[a-z_]+)\]")


class ScriptVersionResponse(ResponseModel):
    """One language version of a script."""

    template_id: uuid.UUID
    language: str
    status: TemplateStatus
    subject: str
    synced_at: datetime | None
    sync_error: str | None
    warnings: int
    source_web_url: str | None
    pdf_web_url: str | None


class ScriptResponse(ResponseModel):
    """A script with its language versions."""

    key: str
    name: str
    service_line: ServiceLine
    versions: dict[str, ScriptVersionResponse]


class LanguageColumnResponse(ResponseModel):
    """A template language and the countries that receive it."""

    code: str
    name: str
    countries: list[str]
    strict: bool = Field(description="Countries here are held (not sent another language).")


class LibraryStatusResponse(ResponseModel):
    """Where scripts live and how the last sync went."""

    provider: str
    location: str
    last_sync_at: datetime | None
    last_sync_status: str
    last_sync_error: str | None
    last_sync_summary: dict[str, object]


class ScriptMatrixResponse(ResponseModel):
    """Every script in every language."""

    languages: list[LanguageColumnResponse]
    scripts: list[ScriptResponse]
    library: LibraryStatusResponse


class BlankScriptRequest(StrictRequestModel):
    """A new script typed in the console (saved to the library as a Word file)."""

    script_name: str = Field(min_length=1, max_length=100, examples=["USA-Canada to country"])
    service_line: ServiceLine = ServiceLine.AIR
    language: str = Field(pattern=LANGUAGE_VARIANT_PATTERN, examples=["en"])
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=20_000)


class TranslateRequest(StrictRequestModel):
    """Languages to translate into (default: every countries-table language missing)."""

    languages: list[str] | None = Field(default=None, max_length=6)


class TranslationOutcomeResponse(ResponseModel):
    """One translation."""

    language: str
    template_id: uuid.UUID | None
    error: str | None


class PreviewRequest(StrictRequestModel):
    """Preview for a real contact, or for a country with sample values."""

    contact_id: uuid.UUID | None = None
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    sender_id: uuid.UUID | None = None


class PreviewResponse(ResponseModel):
    """The email as the recipient would get it."""

    subject: str
    html_body: str | None
    text_body: str
    language: str
    country: str | None
    to_country_phrase: str | None
    sender_id: uuid.UUID | None
    sender_name: str | None
    missing: list[str]


class SyncResponse(ResponseModel):
    """What a sync did."""

    imported: int
    unchanged: int
    failed: int
    deactivated: int
    banners: int
    brand_images: int
    pdfs: int
    rate_sheets: int
    ignored: int
    errors: list[str]


class AssetResponse(ResponseModel):
    """An email image."""

    id: uuid.UUID
    kind: AssetKind
    service_line: ServiceLine
    country: str | None
    filename: str
    content_type: str
    size_bytes: int
    url: str
    updated_at: datetime


async def _tenant(session: TenantSession, principal: CurrentPrincipal) -> Tenant:
    tenant = await session.get(Tenant, principal.tenant_id)
    assert tenant is not None  # noqa: S101 - authenticated tenants exist
    return tenant


TenantRow = Annotated[Tenant, Depends(_tenant)]


async def get_script_library(
    session: TenantSession, container: ContainerDependency, tenant: TenantRow
) -> ScriptLibraryService:
    """The tenant's scripts library for the request."""
    service, _ = await open_script_library(
        session,
        clock=container.clock,
        settings=container.settings,
        http=container.http_client,
        tenant=tenant,
    )
    return service


Library = Annotated[ScriptLibraryService, Depends(get_script_library)]


def _asset_response(asset: EmailAsset, container: ContainerDependency) -> AssetResponse:
    return AssetResponse(
        id=asset.id,
        kind=asset.kind,
        service_line=asset.service_line,
        country=asset.country,
        filename=asset.filename,
        content_type=asset.content_type,
        size_bytes=asset.size_bytes,
        url=asset_url(container.settings, asset.tenant_id, asset.id),
        updated_at=asset.updated_at,
    )


@router.get("", response_model=ScriptMatrixResponse, summary="Scripts by language", responses=READ)
async def list_scripts(
    _principal: Reader, session: TenantSession, container: ContainerDependency
) -> ScriptMatrixResponse:
    """Every script, the language columns from the countries table, and the library status."""
    languages, scripts = await build_script_matrix(session)
    connection = await get_library_connection(session, container.clock)
    location = (
        f"{connection.site_url} / {connection.library_name} / {connection.root_folder}"
        if connection.provider.value == "sharepoint"
        else f"local folder ({container.settings.local_documents_path})"
    )
    return ScriptMatrixResponse(
        languages=[
            LanguageColumnResponse.model_validate(column, from_attributes=True)
            for column in languages
        ],
        scripts=[ScriptResponse.model_validate(script, from_attributes=True) for script in scripts],
        library=LibraryStatusResponse(
            provider=connection.provider.value,
            location=location,
            last_sync_at=connection.last_sync_at,
            last_sync_status=connection.last_sync_status.value,
            last_sync_error=connection.last_sync_error,
            last_sync_summary=dict(connection.last_sync_summary or {}),
        ),
    )


@router.get(
    "/templates/{template_id}",
    response_model=TemplateResponse,
    summary="One script version",
    responses=READ,
)
async def read_script_version(
    template_id: uuid.UUID, _principal: Reader, session: TenantSession
) -> TemplateResponse:
    """A language version with its HTML and import warnings."""
    template = await session.get(Template, template_id)
    if template is None:
        raise NotFoundError("Script not found.")
    return TemplateResponse.model_validate(template)


@router.post(
    "/templates/{template_id}/preview",
    response_model=PreviewResponse,
    summary="Preview a script for a contact or a country",
    responses=WRITE,
)
async def preview_script_version(
    template_id: uuid.UUID,
    payload: PreviewRequest,
    _principal: Reader,
    session: TenantSession,
    container: ContainerDependency,
    tenant: TenantRow,
) -> PreviewResponse:
    """Render exactly what the recipient would receive (nothing is sent or stored)."""
    template = await session.get(Template, template_id)
    if template is None:
        raise NotFoundError("Script not found.")
    contact = await session.get(Contact, payload.contact_id) if payload.contact_id else None
    if payload.contact_id and contact is None:
        raise NotFoundError("Contact not found.")
    sender = await session.get(Sender, payload.sender_id) if payload.sender_id else None
    if payload.sender_id and sender is None:
        raise NotFoundError("Agent not found.")
    rendered, country, used_sender = await preview_script(
        session,
        container.settings,
        tenant,
        template,
        contact=contact,
        country_code=payload.country,
        sender=sender,
    )
    missing = sorted(set(_MISSING.findall(rendered.subject + rendered.body)))
    return PreviewResponse(
        subject=rendered.subject,
        html_body=rendered.html_body,
        text_body=rendered.body,
        language=template.language,
        country=country.country if country else (contact.country if contact else payload.country),
        to_country_phrase=(destination_phrase(country, template.language) if country else None),
        sender_id=used_sender.id if used_sender else None,
        sender_name=used_sender.display_name if used_sender else None,
        missing=missing,
    )


@router.post(
    "/upload",
    response_model=TemplateResponse | None,
    summary="Upload a Word or PDF script (saved to SharePoint, then imported)",
    description="Word files are imported right away; a PDF is kept as the reference copy of "
    "the Word version with the same name and language.",
    responses=WRITE,
)
async def upload_script(
    _principal: Writer,
    library: Library,
    idempotency: IdempotencyGuardDependency,
    file: Annotated[UploadFile, File(description="The .docx or .pdf script.")],
    script_name: Annotated[str, Form(min_length=1, max_length=100)],
    language: Annotated[str, Form(pattern=LANGUAGE_VARIANT_PATTERN)],
    service_line: Annotated[ServiceLine, Form()] = ServiceLine.AIR,
) -> TemplateResponse | JSONResponse | None:
    """Save and import an uploaded script."""
    content = await file.read()

    async def upload() -> TemplateResponse | None:
        template = await library.upload_script(
            script_name=script_name,
            service_line=service_line,
            language=language,
            filename=file.filename or "script.docx",
            content=content,
        )
        return TemplateResponse.model_validate(template) if template else None

    return await run_idempotent(idempotency, status.HTTP_200_OK, upload)


@router.post(
    "/blank",
    response_model=TemplateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a script from text (written to SharePoint as a Word file)",
    responses=WRITE,
)
async def create_blank_script(
    payload: BlankScriptRequest,
    _principal: Writer,
    library: Library,
    idempotency: IdempotencyGuardDependency,
) -> TemplateResponse | JSONResponse:
    """Create a script from the console."""

    async def create() -> TemplateResponse:
        return TemplateResponse.model_validate(
            await library.create_blank_script(**payload.model_dump())
        )

    return await run_idempotent(idempotency, status.HTTP_201_CREATED, create)


@router.post(
    "/sync", response_model=SyncResponse, summary="Sync from SharePoint now", responses=WRITE
)
async def sync_scripts(
    _principal: Writer,
    session: TenantSession,
    container: ContainerDependency,
    tenant: TenantRow,
) -> SyncResponse:
    """Import every change in the library now (it also runs every few minutes)."""
    summary = await sync_tenant_library(
        session,
        clock=container.clock,
        settings=container.settings,
        http=container.http_client,
        tenant=tenant,
    )
    return SyncResponse.model_validate(summary.to_json())


@router.post(
    "/{key}/translate",
    response_model=list[TranslationOutcomeResponse],
    summary="AI-translate a script into missing languages (drafts, need approval)",
    dependencies=[AIRateLimit],
    responses=WRITE,
)
async def translate_script(
    key: str,
    payload: TranslateRequest,
    _principal: Writer,
    library: Library,
    runtime: Annotated[AIRuntime, Depends(get_ai_runtime)],
    idempotency: IdempotencyGuardDependency,
) -> list[TranslationOutcomeResponse] | JSONResponse:
    """Translate into up to six languages per call."""

    async def translate() -> list[TranslationOutcomeResponse]:
        outcomes = await library.translate_missing_languages(key, runtime, payload.languages)
        return [
            TranslationOutcomeResponse.model_validate(item, from_attributes=True)
            for item in outcomes
        ]

    return await run_idempotent(idempotency, status.HTTP_200_OK, translate)


@router.post(
    "/templates/{template_id}/approve",
    response_model=TemplateResponse,
    summary="Approve an AI-translated script so it can be sent",
    responses=WRITE,
)
async def approve_script(
    template_id: uuid.UUID,
    _principal: Publisher,
    session: TenantSession,
    container: ContainerDependency,
    idempotency: IdempotencyGuardDependency,
) -> TemplateResponse | JSONResponse:
    """Approve an AI draft."""

    async def approve() -> TemplateResponse:
        return TemplateResponse.model_validate(
            await approve_ai_draft(session, container.clock, template_id)
        )

    return await run_idempotent(idempotency, status.HTTP_200_OK, approve)


@router.post(
    "/banners",
    response_model=AssetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a country banner (saved to SharePoint, then imported)",
    responses=WRITE,
)
async def upload_banner(
    _principal: Writer,
    library: Library,
    container: ContainerDependency,
    idempotency: IdempotencyGuardDependency,
    file: Annotated[UploadFile, File(description="PNG, JPEG, GIF or WebP.")],
    service_line: Annotated[ServiceLine, Form()] = ServiceLine.AIR,
    country: Annotated[str | None, Form(pattern=r"^[A-Z]{2}$")] = None,
) -> AssetResponse | JSONResponse:
    """Upload a banner (no country = the default banner of the service line)."""
    content = await file.read()

    async def upload() -> AssetResponse:
        asset = await library.upload_banner(
            service_line=service_line,
            country=country,
            filename=file.filename or "banner.png",
            content=content,
        )
        return _asset_response(asset, container)

    return await run_idempotent(idempotency, status.HTTP_201_CREATED, upload)


@router.post(
    "/brand-images",
    response_model=AssetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a logo, badge, QR code or promo banner for signatures",
    description="Saved into the library's `Brand/` folder and linked in the branding: the logo, "
    "QR code and promo banner replace the current one; badges join the badge row.",
    responses=WRITE,
)
async def upload_brand_image(
    _principal: Writer,
    library: Library,
    container: ContainerDependency,
    idempotency: IdempotencyGuardDependency,
    file: Annotated[UploadFile, File(description="PNG, JPEG, GIF or WebP.")],
    kind: Annotated[AssetKind, Form(description="logo, badge, qr or promo.")] = AssetKind.BADGE,
) -> AssetResponse | JSONResponse:
    """Upload a brand image."""
    content = await file.read()

    async def upload() -> AssetResponse:
        asset = await library.upload_brand_image(
            kind=kind, filename=file.filename or f"{kind.value}.png", content=content
        )
        return _asset_response(asset, container)

    return await run_idempotent(idempotency, status.HTTP_201_CREATED, upload)


@router.get("/assets", response_model=list[AssetResponse], summary="Email images", responses=READ)
async def list_assets(
    _principal: Reader,
    session: TenantSession,
    container: ContainerDependency,
    kind: Annotated[AssetKind | None, Query()] = None,
) -> list[AssetResponse]:
    """Banners, logos, badges and inline images."""
    assets = await AssetService(session, container.clock).list_assets(kind)
    return [_asset_response(asset, container) for asset in assets]
