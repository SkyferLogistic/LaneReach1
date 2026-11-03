"""Scripts library: sync from SharePoint, uploads, blank scripts, AI translations, previews.

SharePoint (or the local folder in development) is the master copy. The sync imports every
Word script and banner that changed, keeps the last good version of a script when its new
version cannot be imported, and deactivates scripts whose file was removed. Scripts created in
the console are written to the library first and imported from there, so both paths end in the
same place.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from lanereach.ai.llm.client import LLMError
from lanereach.ai.tasks.localize_template import localize_template
from lanereach.ai.tasks.runtime import AIRuntime
from lanereach.automation.engine.email import (
    RenderedEmail,
    personalise_for_agent,
    render_email,
    template_values,
    unsubscribe_url_for,
)
from lanereach.core.clock import Clock
from lanereach.core.config import Settings
from lanereach.core.errors import BusinessRuleViolationError, NotFoundError
from lanereach.core.languages import (
    is_language_variant,
    language_name,
    split_language_variant,
)
from lanereach.core.tenancy import require_session_tenant_id
from lanereach.domain.assets.models import AssetKind, EmailAsset
from lanereach.domain.assets.service import AssetService, detect_image_type
from lanereach.domain.contacts.models import Contact
from lanereach.domain.countries.models import Country
from lanereach.domain.countries.service import CountryService, find_country
from lanereach.domain.offices.models import Office
from lanereach.domain.scripts.importer import (
    ImportedScript,
    ScriptImportError,
    convert_word_script,
    html_to_text,
)
from lanereach.domain.scripts.paths import (
    BannerFile,
    BrandFile,
    RateSheetFile,
    ScriptFile,
    banner_path,
    parse_library_path,
    script_key,
    script_path,
)
from lanereach.domain.scripts.word import build_word_script
from lanereach.domain.senders.models import Sender, ServiceLine
from lanereach.domain.templates.models import Template, TemplateSource, TemplateStatus
from lanereach.integrations.documents.store import (
    DocumentItem,
    DocumentStore,
    guess_content_type,
)
from lanereach.tenants.branding import load_branding
from lanereach.tenants.models import Tenant

logger = structlog.get_logger(__name__)

MAX_TRANSLATIONS_PER_REQUEST = 6
_SCRIPT_NAME = re.compile(r"^[^/\\<>:\"|?*]{1,100}$")
_IMAGE_REFERENCE = re.compile(r"lr-image://(\d+)")
_IMG_WITH_REFERENCE = r'<img[^>]*src="lr-image://{index}"[^>]*>'


BRAND_KINDS = (AssetKind.LOGO, AssetKind.BADGE, AssetKind.QR, AssetKind.PROMO)
MAX_BADGES = 10


def brand_image_kind(name: str) -> AssetKind:
    """What a `Brand/<name>` image is: `logo*`, `qr*`, `promo*`, anything else a badge."""
    lowered = name.lower()
    if lowered.startswith("logo"):
        return AssetKind.LOGO
    if lowered.startswith("qr"):
        return AssetKind.QR
    if lowered.startswith("promo"):
        return AssetKind.PROMO
    return AssetKind.BADGE


def _slug(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0]
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:40] or "badge"


@dataclass
class SyncSummary:
    """What one sync did."""

    imported: int = 0
    unchanged: int = 0
    failed: int = 0
    deactivated: int = 0
    banners: int = 0
    brand_images: int = 0
    pdfs: int = 0
    rate_sheets: int = 0
    ignored: int = 0
    errors: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        """JSON form stored on the connection."""
        return {
            "imported": self.imported,
            "unchanged": self.unchanged,
            "failed": self.failed,
            "deactivated": self.deactivated,
            "banners": self.banners,
            "brand_images": self.brand_images,
            "pdfs": self.pdfs,
            "rate_sheets": self.rate_sheets,
            "ignored": self.ignored,
            "errors": self.errors[:20],
        }


@dataclass(frozen=True, slots=True)
class TranslationOutcome:
    """Result of translating a script into one language."""

    language: str
    template_id: uuid.UUID | None
    error: str | None


def validate_script_name(name: str) -> str:
    """A script name usable as a SharePoint folder name.

    Raises:
        BusinessRuleViolationError: For empty names, slashes or characters SharePoint refuses.
    """
    cleaned = " ".join(name.split())
    if not _SCRIPT_NAME.match(cleaned):
        raise BusinessRuleViolationError(
            'Script names are 1-100 characters and cannot contain / \\ < > : " | ? *'
        )
    return cleaned


class ScriptLibraryService:
    """Keeps a tenant's templates in step with its scripts library.

    Args:
        session: Tenant session.
        clock: Time source.
        settings: Upload limits.
        store: The tenant's library.
        tenant: The tenant.
    """

    def __init__(
        self,
        session: AsyncSession,
        clock: Clock,
        settings: Settings,
        store: DocumentStore,
        tenant: Tenant,
    ) -> None:
        self._session = session
        self._clock = clock
        self._settings = settings
        self._store = store
        self._tenant = tenant
        self._assets = AssetService(session, clock)

    # --- sync --------------------------------------------------------------------------

    async def sync_library(self) -> SyncSummary:
        """Import everything that changed in the library since the last sync.

        Returns:
            Counts and the first errors.
        """
        summary = SyncSummary()
        items = await self._store.list_files()
        known = await self._session.scalar(
            select(Template.id).where(Template.source == TemplateSource.SHAREPOINT).limit(1)
        )
        if not items and known is not None:
            # An empty listing for a library that had scripts is far more likely a wrong folder
            # or a lost volume than marketing deleting everything: change nothing.
            summary.errors.append("The library is empty or unreachable; nothing was changed.")
            return summary
        seen_scripts: set[str] = set()
        seen_images: set[str] = set()
        pdfs: list[tuple[DocumentItem, ScriptFile]] = []
        rate_items: list[DocumentItem] = []
        for item in items:
            parsed = parse_library_path(item.path)
            if isinstance(parsed, ScriptFile) and parsed.extension == "docx":
                seen_scripts.add(item.item_id)
                await self._sync_script(item, parsed, summary)
            elif isinstance(parsed, ScriptFile):
                pdfs.append((item, parsed))
            elif isinstance(parsed, BannerFile):
                seen_images.add(item.item_id)
                await self._sync_banner(item, parsed, summary)
            elif isinstance(parsed, BrandFile):
                seen_images.add(item.item_id)
                await self._sync_brand_image(item, parsed, summary)
            elif isinstance(parsed, RateSheetFile):
                rate_items.append(item)
            else:
                summary.ignored += 1
        for item, parsed in pdfs:
            await self._attach_pdf(item, parsed, summary)
        await self._sync_rate_sheets(items, rate_items, summary)
        summary.deactivated = await self._deactivate_missing(seen_scripts)
        await self._remove_missing_images(seen_images)
        await self._refresh_brand_links()
        logger.info(
            "script_library_synced", **{k: v for k, v in summary.to_json().items() if k != "errors"}
        )
        return summary

    async def _sync_rate_sheets(
        self, items: list[DocumentItem], rate_items: list[DocumentItem], summary: SyncSummary
    ) -> None:
        """Import changed pricing sheets from `Rates/` (and drop the ones removed there)."""
        # Imported here: pricing depends on quotes and the AI layer, not on the scripts library.
        from lanereach.domain.pricing.service import RateSheetService

        rates = await RateSheetService(self._session, self._clock).sync_from_library(
            self._store, rate_items, remove_missing=bool(items)
        )
        summary.rate_sheets = rates.imported
        summary.unchanged += rates.unchanged
        summary.failed += rates.failed
        summary.errors.extend(rates.errors)

    async def _sync_script(
        self, item: DocumentItem, parsed: ScriptFile, summary: SyncSummary
    ) -> None:
        existing = await self._find_template(item, parsed)
        if (
            existing is not None
            and existing.source_etag == item.etag
            and existing.status is not TemplateStatus.ERROR
            and existing.sync_error is None
        ):
            summary.unchanged += 1
            return
        try:
            content = await self._store.download(item)
            await self.import_script(item, parsed, content)
            summary.imported += 1
        except ScriptImportError as exc:
            summary.failed += 1
            summary.errors.append(f"{item.path}: {exc}")
            await self._record_failure(item, parsed, existing, str(exc))

    async def import_script(
        self, item: DocumentItem, parsed: ScriptFile, content: bytes
    ) -> Template:
        """Import one Word script into its template (creating it the first time).

        Args:
            item: The library file.
            parsed: What the path says (service line, script name, language).
            content: The Word document.

        Returns:
            The template.

        Raises:
            ScriptImportError: When the document cannot be used.
        """
        converted = convert_word_script(content, language=parsed.language)
        html_body = await self._store_images(item, parsed, converted)
        now = self._clock.now()
        template = await self._find_template(item, parsed)
        if template is None:
            template = Template(
                tenant_id=require_session_tenant_id(self._session),
                key=parsed.key,
                language=parsed.language,
                status=TemplateStatus.ACTIVE,
                created_at=now,
            )
            self._session.add(template)
        elif template.status in (TemplateStatus.INACTIVE, TemplateStatus.ERROR):
            template.status = TemplateStatus.ACTIVE
        # An AI translation stays a draft until someone approves it, even if edited in Word.
        template.key = parsed.key
        template.language = parsed.language
        template.name = parsed.script_name[:200]
        template.subject = converted.subject[:300]
        template.body = converted.text_body
        template.html_body = html_body
        template.service_line = parsed.service_line
        template.source = TemplateSource.SHAREPOINT
        template.source_item_id = item.item_id
        template.source_path = item.path
        template.source_etag = item.etag
        template.source_web_url = item.web_url
        template.synced_at = now
        template.sync_error = None
        template.import_warnings = converted.warnings
        template.updated_at = now
        await self._session.flush()
        return template

    async def _store_images(
        self, item: DocumentItem, parsed: ScriptFile, converted: ImportedScript
    ) -> str:
        html_body = converted.html_body
        await self._assets.delete_by_source_prefix(f"{item.item_id}#")
        for index, image in enumerate(converted.images):
            try:
                asset = await self._assets.store_image(
                    kind=AssetKind.INLINE,
                    filename=f"{parsed.script_name}-{parsed.language}-{index + 1}",
                    content=image.content,
                    service_line=parsed.service_line,
                    source_item_id=f"{item.item_id}#{index}",
                    source_etag=item.etag,
                )
            except BusinessRuleViolationError as exc:
                # Word can embed EMF/WMF drawings mail clients cannot show: drop them, say so.
                converted.warnings.append(f"Image {index + 1} was removed: {exc}")
                html_body = re.sub(_IMG_WITH_REFERENCE.format(index=index), "", html_body)
                continue
            html_body = html_body.replace(f"lr-image://{index}", f"lr-asset://{asset.id}")
        return html_body

    async def _record_failure(
        self, item: DocumentItem, parsed: ScriptFile, existing: Template | None, error: str
    ) -> None:
        now = self._clock.now()
        if existing is not None and existing.html_body and existing.status is TemplateStatus.ACTIVE:
            # Keep sending the last good version; show why the new one was not taken.
            existing.sync_error = error
            existing.source_web_url = item.web_url
            existing.updated_at = now
            return
        if existing is None:
            existing = Template(
                tenant_id=require_session_tenant_id(self._session),
                key=parsed.key,
                language=parsed.language,
                name=parsed.script_name[:200],
                subject="(not imported)",
                body="",
                service_line=parsed.service_line,
                source=TemplateSource.SHAREPOINT,
                created_at=now,
            )
            self._session.add(existing)
        existing.status = TemplateStatus.ERROR
        existing.sync_error = error
        existing.source_item_id = item.item_id
        existing.source_path = item.path
        existing.source_etag = item.etag
        existing.source_web_url = item.web_url
        existing.synced_at = now
        existing.import_warnings = []
        existing.updated_at = now
        await self._session.flush()

    async def _find_template(self, item: DocumentItem, parsed: ScriptFile) -> Template | None:
        by_item = await self._session.scalar(
            select(Template).where(Template.source_item_id == item.item_id)
        )
        if by_item is not None:
            return by_item
        return await self._session.scalar(
            select(Template).where(Template.key == parsed.key, Template.language == parsed.language)
        )

    async def _attach_pdf(
        self, item: DocumentItem, parsed: ScriptFile, summary: SyncSummary
    ) -> None:
        template = await self._session.scalar(
            select(Template).where(Template.key == parsed.key, Template.language == parsed.language)
        )
        if template is None:
            summary.errors.append(f"{item.path}: PDF without a Word version (upload the .docx)")
            return
        template.pdf_web_url = item.web_url
        summary.pdfs += 1

    async def _sync_banner(
        self, item: DocumentItem, parsed: BannerFile, summary: SyncSummary
    ) -> None:
        country = await self._resolve_country(parsed.country)
        if parsed.country is not None and country is None:
            summary.errors.append(f"{item.path}: unknown country {parsed.country!r}")
            return
        existing = await self._session.scalar(
            select(EmailAsset).where(EmailAsset.source_item_id == item.item_id)
        )
        if existing is not None and existing.source_etag == item.etag:
            return
        try:
            await self._assets.store_image(
                kind=AssetKind.BANNER,
                filename=item.name,
                content=await self._store.download(item),
                service_line=parsed.service_line,
                country=country,
                source_item_id=item.item_id,
                source_etag=item.etag,
            )
            summary.banners += 1
        except BusinessRuleViolationError as exc:
            summary.errors.append(f"{item.path}: {exc}")

    async def _sync_brand_image(
        self, item: DocumentItem, parsed: BrandFile, summary: SyncSummary
    ) -> None:
        existing = await self._session.scalar(
            select(EmailAsset).where(EmailAsset.source_item_id == item.item_id)
        )
        if existing is not None and existing.source_etag == item.etag:
            return
        kind = brand_image_kind(parsed.name)
        try:
            await self._assets.store_image(
                kind=kind,
                filename=item.name,
                content=await self._store.download(item),
                source_item_id=item.item_id,
                source_etag=item.etag,
            )
            summary.brand_images += 1
        except BusinessRuleViolationError as exc:
            summary.errors.append(f"{item.path}: {exc}")

    async def _resolve_country(self, label: str | None) -> str | None:
        if label is None:
            return None
        if re.fullmatch(r"[A-Z]{2}", label):
            return label
        row = await CountryService(self._session, self._clock).find_by_name(label)
        return row.country if row else None

    async def _deactivate_missing(self, seen: set[str]) -> int:
        rows = (
            await self._session.scalars(
                select(Template).where(
                    Template.source == TemplateSource.SHAREPOINT,
                    Template.status != TemplateStatus.INACTIVE,
                )
            )
        ).all()
        count = 0
        for template in rows:
            if template.source_item_id not in seen:
                template.status = TemplateStatus.INACTIVE
                template.sync_error = "The Word file was removed from the library."
                template.updated_at = self._clock.now()
                count += 1
        return count

    async def _remove_missing_images(self, seen: set[str]) -> None:
        library_images = (
            await self._session.scalars(
                select(EmailAsset).where(
                    EmailAsset.kind.in_([AssetKind.BANNER, *BRAND_KINDS]),
                    EmailAsset.source_item_id.is_not(None),
                )
            )
        ).all()
        gone = [asset.id for asset in library_images if asset.source_item_id not in seen]
        if gone:
            await self._session.execute(
                delete(EmailAsset)
                .where(EmailAsset.id.in_(gone))
                .execution_options(synchronize_session=False)
            )

    async def _refresh_brand_links(self) -> None:
        """Point the branding at the library's `Brand/logo*`, `qr*`, `promo*`, `badge*` images."""
        images = (
            await self._session.scalars(
                select(EmailAsset)
                .where(
                    EmailAsset.kind.in_(BRAND_KINDS),
                    EmailAsset.source_item_id.is_not(None),
                )
                .order_by(EmailAsset.filename)
            )
        ).all()
        if not images:
            return
        branding = load_branding(self._tenant.branding)
        logo = next((asset for asset in images if asset.kind is AssetKind.LOGO), None)
        badges = [asset.id for asset in images if asset.kind is AssetKind.BADGE]
        qr = next((asset for asset in images if asset.kind is AssetKind.QR), None)
        promo = next((asset for asset in images if asset.kind is AssetKind.PROMO), None)
        branding.logo_asset_id = logo.id if logo else branding.logo_asset_id
        branding.qr_asset_id = qr.id if qr else branding.qr_asset_id
        branding.promo_asset_id = promo.id if promo else branding.promo_asset_id
        branding.badge_asset_ids = badges or branding.badge_asset_ids
        self._tenant.branding = branding.model_dump(mode="json")

    # --- console actions ---------------------------------------------------------------

    async def upload_script(
        self,
        *,
        script_name: str,
        service_line: ServiceLine,
        language: str,
        filename: str,
        content: bytes,
    ) -> Template | None:
        """Save an uploaded Word or PDF script into the library and import it.

        Args:
            script_name: Script (folder) name, e.g. `USA-Canada to country`.
            service_line: Air, ocean or any.
            language: Template language.
            filename: Uploaded file name (decides Word or PDF).
            content: File bytes.

        Returns:
            The template (for a PDF: the Word version it belongs to, if already imported).

        Raises:
            BusinessRuleViolationError: For other file types, oversized files, a bad language
                or a Word file that cannot be imported (nothing is saved then).
        """
        name = validate_script_name(script_name)
        self._check_language(language)
        self._check_size(content)
        extension = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
        if extension not in {"docx", "pdf"}:
            raise BusinessRuleViolationError("Upload a Word (.docx) or PDF file.")
        parsed = ScriptFile(service_line, name, language, extension)
        if extension == "docx":
            try:
                convert_word_script(content, language=language)  # refuse before saving
            except ScriptImportError as exc:
                raise BusinessRuleViolationError(str(exc)) from exc
        item = await self._store.upload(
            script_path(service_line, name, language, extension),
            content,
            guess_content_type(filename),
        )
        if extension == "pdf":
            summary = SyncSummary()
            await self._attach_pdf(item, parsed, summary)
            return await self._session.scalar(
                select(Template).where(Template.key == parsed.key, Template.language == language)
            )
        return await self.import_script(item, parsed, content)

    async def create_blank_script(
        self,
        *,
        script_name: str,
        service_line: ServiceLine,
        language: str,
        subject: str,
        body: str,
    ) -> Template:
        """Write a new Word script from text typed in the console, then import it.

        Raises:
            BusinessRuleViolationError: For a bad name/language or text that cannot be imported.
        """
        name = validate_script_name(script_name)
        self._check_language(language)
        document = build_word_script(subject, body)
        parsed = ScriptFile(service_line, name, language, "docx")
        try:
            convert_word_script(document, language=language)
        except ScriptImportError as exc:
            raise BusinessRuleViolationError(str(exc)) from exc
        item = await self._store.upload(
            script_path(service_line, name, language, "docx"),
            document,
            guess_content_type("x.docx"),
        )
        return await self.import_script(item, parsed, document)

    async def upload_banner(
        self, *, service_line: ServiceLine, country: str | None, filename: str, content: bytes
    ) -> EmailAsset:
        """Save a banner into the library (`Banners/<Line>/<CC>.<ext>`) and import it.

        Raises:
            BusinessRuleViolationError: For non-images or unknown countries.
        """
        self._check_size(content)
        extension = filename.lower().rsplit(".", 1)[-1] if "." in filename else "png"
        if country is not None and await find_country(self._session, country) is None:
            raise BusinessRuleViolationError(f"Country {country} is not in the countries table.")
        detect_image_type(content)
        item = await self._store.upload(
            banner_path(service_line, country, extension), content, guess_content_type(filename)
        )
        return await self._assets.store_image(
            kind=AssetKind.BANNER,
            filename=item.name,
            content=content,
            service_line=service_line,
            country=country,
            source_item_id=item.item_id,
            source_etag=item.etag,
        )

    async def upload_brand_image(
        self, *, kind: AssetKind, filename: str, content: bytes
    ) -> EmailAsset:
        """Save a logo, badge, QR code or promo banner into the library's `Brand/` folder.

        The logo, QR code and promo banner replace the current one in the branding; badges are
        added to the badge row (up to ten).

        Raises:
            BusinessRuleViolationError: For non-images, other kinds or a full badge row.
        """
        if kind not in BRAND_KINDS:
            raise BusinessRuleViolationError("Brand images are logos, badges, QR codes or promos.")
        self._check_size(content)
        detect_image_type(content)
        branding = load_branding(self._tenant.branding)
        if kind is AssetKind.BADGE and len(branding.badge_asset_ids) >= MAX_BADGES:
            raise BusinessRuleViolationError(f"The badge row holds at most {MAX_BADGES} badges.")
        extension = filename.lower().rsplit(".", 1)[-1] if "." in filename else "png"
        stem = kind.value if kind is not AssetKind.BADGE else f"badge-{_slug(filename)}"
        item = await self._store.upload(
            f"Brand/{stem}.{extension}", content, guess_content_type(filename)
        )
        asset = await self._assets.store_image(
            kind=kind,
            filename=item.name,
            content=content,
            source_item_id=item.item_id,
            source_etag=item.etag,
        )
        if kind is AssetKind.LOGO:
            branding.logo_asset_id = asset.id
        elif kind is AssetKind.QR:
            branding.qr_asset_id = asset.id
        elif kind is AssetKind.PROMO:
            branding.promo_asset_id = asset.id
        elif asset.id not in branding.badge_asset_ids:
            branding.badge_asset_ids = [*branding.badge_asset_ids, asset.id]
        self._tenant.branding = branding.model_dump(mode="json")
        return asset

    async def translate_missing_languages(
        self, key: str, runtime: AIRuntime, languages: Sequence[str] | None
    ) -> list[TranslationOutcome]:
        """AI-translate a script into languages it lacks; results wait for approval.

        The source is the English version (else any active one). Each translation is written
        to the library as a Word file (so marketing can polish it in SharePoint) and imported
        as an `ai_draft`, which is never sent until approved.

        Args:
            key: Template key.
            runtime: AI runtime.
            languages: Target languages; defaults to every language of the countries table
                that has no version yet (at most six per call).

        Returns:
            One outcome per language.

        Raises:
            NotFoundError: If the script has no active version to translate from.
        """
        versions = (await self._session.scalars(select(Template).where(Template.key == key))).all()
        active = [row for row in versions if row.status is TemplateStatus.ACTIVE and row.html_body]
        source = next(
            (row for row in active if row.language == "en"), active[0] if active else None
        )
        if source is None:
            raise NotFoundError("This script has no active version to translate from.")
        existing = {row.language for row in versions}
        if languages is None:
            wanted = [
                code
                for code in dict.fromkeys(
                    row.language
                    for row in (await self._session.scalars(select(Country))).all()
                    if row.is_enabled
                )
                if code not in existing
            ]
        else:
            wanted = [code for code in dict.fromkeys(languages) if code not in existing]
        outcomes: list[TranslationOutcome] = []
        body = html_to_text(source.html_body or source.body, keep_blocks=True)
        for language in wanted[:MAX_TRANSLATIONS_PER_REQUEST]:
            self._check_language(language)
            base, region = split_language_variant(language)
            try:
                result = await localize_template(
                    runtime,
                    subject=source.subject,
                    body=body,
                    target_language=base,
                    region=region,
                    input_ref=str(source.id),
                )
            except LLMError as exc:
                outcomes.append(
                    TranslationOutcome(language, None, f"AI unavailable ({type(exc).__name__})")
                )
                continue
            document = build_word_script(result.output.subject, result.output.body)
            parsed = ScriptFile(source.service_line, source.name, language, "docx")
            item = await self._store.upload(
                script_path(source.service_line, source.name, language, "docx"),
                document,
                guess_content_type("x.docx"),
            )
            try:
                template = await self.import_script(item, parsed, document)
            except ScriptImportError as exc:
                outcomes.append(TranslationOutcome(language, None, str(exc)))
                continue
            template.status = TemplateStatus.AI_DRAFT
            template.import_warnings = [
                "Translated by AI: check it (in Word if needed) and approve it before it is sent.",
                *template.import_warnings,
            ]
            outcomes.append(TranslationOutcome(language, template.id, None))
        await self._session.flush()
        return outcomes

    def _check_language(self, language: str) -> None:
        if not is_language_variant(language):
            raise BusinessRuleViolationError(f"{language!r} is not a template language code.")

    def _check_size(self, content: bytes) -> None:
        if len(content) > self._settings.max_upload_bytes:
            limit = self._settings.max_upload_bytes // (1024 * 1024)
            raise BusinessRuleViolationError(f"Files must be {limit} MB or smaller.")


async def approve_ai_draft(session: AsyncSession, clock: Clock, template_id: uuid.UUID) -> Template:
    """Allow an AI-translated script to be sent.

    Raises:
        NotFoundError: If the template does not exist.
        BusinessRuleViolationError: If it is not an AI draft.
    """
    template = await session.get(Template, template_id)
    if template is None:
        raise NotFoundError("Script not found.")
    if template.status is not TemplateStatus.AI_DRAFT:
        raise BusinessRuleViolationError("Only AI drafts need approval.")
    template.status = TemplateStatus.ACTIVE
    template.import_warnings = [
        warning
        for warning in template.import_warnings
        if not str(warning).startswith("Translated by AI")
    ]
    template.updated_at = clock.now()
    return template


# --- read models -------------------------------------------------------------------------


@dataclass
class ScriptVersionView:
    """One language version in the matrix."""

    template_id: uuid.UUID
    language: str
    status: TemplateStatus
    subject: str
    synced_at: Any
    sync_error: str | None
    warnings: int
    source_web_url: str | None
    pdf_web_url: str | None


@dataclass
class ScriptView:
    """One script (a key) with its language versions."""

    key: str
    name: str
    service_line: ServiceLine
    versions: dict[str, ScriptVersionView]


@dataclass
class LanguageColumn:
    """A language of the countries table (or of an existing script)."""

    code: str
    name: str
    countries: list[str]
    strict: bool


async def build_script_matrix(
    session: AsyncSession,
) -> tuple[list[LanguageColumn], list[ScriptView]]:
    """Scripts by language, for the console's matrix.

    Returns:
        Language columns (countries table order, then any other language used) and scripts.
    """
    countries = (await session.scalars(select(Country).order_by(Country.name))).all()
    templates = (
        await session.scalars(select(Template).order_by(Template.key, Template.language))
    ).all()
    columns: dict[str, LanguageColumn] = {}
    for row in countries:
        column = columns.setdefault(
            row.language, LanguageColumn(row.language, language_name(row.language), [], False)
        )
        column.countries.append(row.country)
        column.strict = column.strict or not row.allow_language_fallback
    for template in templates:
        columns.setdefault(
            template.language,
            LanguageColumn(template.language, language_name(template.language), [], False),
        )
    scripts: dict[str, ScriptView] = {}
    for template in templates:
        view = scripts.setdefault(
            template.key, ScriptView(template.key, template.name, template.service_line, {})
        )
        view.versions[template.language] = ScriptVersionView(
            template_id=template.id,
            language=template.language,
            status=template.status,
            subject=template.subject,
            synced_at=template.synced_at,
            sync_error=template.sync_error,
            warnings=len(template.import_warnings or []),
            source_web_url=template.source_web_url,
            pdf_web_url=template.pdf_web_url,
        )
    ordered = sorted(
        columns.values(),
        key=lambda column: (column.code != "en", -len(column.countries), column.code),
    )
    return ordered, list(scripts.values())


async def preview_script(
    session: AsyncSession,
    settings: Settings,
    tenant: Tenant,
    template: Template,
    *,
    contact: Contact | None,
    country_code: str | None,
    sender: Sender | None,
) -> tuple[RenderedEmail, Country | None, Sender | None]:
    """Render a script exactly as a recipient would get it (missing values shown as `[name]`).

    Args:
        session: Tenant session.
        settings: Public links.
        tenant: The tenant.
        template: The version to preview.
        contact: A real contact, or None to use sample values.
        country_code: Country to preview for when no contact is given.
        sender: The agent; defaults to the first agent of the script's service line.

    Returns:
        The rendered email, the country row used and the agent used.
    """
    code = contact.country if contact else country_code
    country = await find_country(session, code)
    recipient = contact or Contact(
        tenant_id=tenant.id,
        email="agent@example.com",
        first_name="Alex",
        last_name="Sample",
        country=code,
        network="WCA",
        tags=[],
    )
    if sender is None:
        agents = (
            await session.scalars(
                select(Sender).where(Sender.is_active.is_(True)).order_by(Sender.display_name)
            )
        ).all()
        # An agent of the script's own service line first, then an "any" agent.
        ranked = sorted(
            (a for a in agents if a.service_line in (template.service_line, ServiceLine.ANY)),
            key=lambda agent: agent.service_line is not template.service_line,
        )
        sender = ranked[0] if ranked else None
    office = (
        await session.get(Office, sender.office_id)
        if sender
        else await session.scalar(select(Office).limit(1))
    )
    if sender is None or office is None:
        raise BusinessRuleViolationError("Add an office and an agent to preview scripts.")
    extras = await personalise_for_agent(
        session,
        settings,
        tenant=tenant,
        sender=sender,
        template=template,
        country_code=code,
        office=office,
    )
    values = template_values(
        contact=recipient,
        company=None,
        office=office,
        sender=sender,
        unsubscribe_url=unsubscribe_url_for(settings, tenant.id, recipient.id or uuid.uuid4()),
        country=country,
        signature=extras.signature,
        signature_text=extras.signature_text,
        banner=extras.banner,
        template_language=template.language,
        office_signature=extras.office_signature,
    )
    values["company.name"] = values.get("company.name") or "Sample Forwarding Co."
    rendered = render_email(template, values, settings=settings, lenient=True)
    return rendered, country, sender


def script_key_for(service_line: ServiceLine, script_name: str) -> str:
    """Template key a script name gets (shown before uploading)."""
    return script_key(service_line, validate_script_name(script_name))
