"""SharePoint document library through Microsoft Graph (app-only, client credentials).

The tenant registers an Azure AD app with the Graph `Sites.Selected` application permission and
grants it the SharePoint site. LaneReach then walks the LaneReach folder, downloads changed
Word files and banners, and uploads scripts created in the console. The client secret comes
from `SHAREPOINT_CLIENT_SECRETS` (environment only).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
import structlog

from lanereach.core.clock import Clock
from lanereach.integrations.documents.store import (
    DocumentItem,
    DocumentStoreError,
    clean_library_path,
)

logger = structlog.get_logger(__name__)

GRAPH_SCOPE = "https://graph.microsoft.com/.default"
_TOKEN_REFRESH_MARGIN = timedelta(minutes=2)
_MAX_SIMPLE_UPLOAD = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SharePointSettings:
    """Where the library is and how to authenticate."""

    graph_base_url: str
    login_base_url: str
    azure_tenant_id: str
    client_id: str
    client_secret: str
    site_url: str
    library_name: str
    root_folder: str


class SharePointDocumentStore:
    """A SharePoint document library folder.

    Args:
        http: Shared HTTP client.
        settings: Library location and app credentials.
        clock: Time source (token expiry).
    """

    kind = "sharepoint"

    def __init__(self, http: httpx.AsyncClient, settings: SharePointSettings, clock: Clock) -> None:
        self._http = http
        self._settings = settings
        self._clock = clock
        self._token: str | None = None
        self._token_expires_at: datetime | None = None
        self._drive_id: str | None = None
        self._root = clean_library_path(settings.root_folder)

    async def list_files(self) -> list[DocumentItem]:
        """Every file under the LaneReach folder (walks the folders; a missing folder is empty)."""
        drive = await self._drive()
        base = self._settings.graph_base_url
        select = "$select=id,name,file,folder,size,cTag,eTag,lastModifiedDateTime,webUrl&$top=200"
        items: list[DocumentItem] = []
        pending: list[str] = [""]
        while pending:
            folder = pending.pop()
            location = quote(f"{self._root}/{folder}".rstrip("/"))
            url: str | None = f"{base}/drives/{drive}/root:/{location}:/children?{select}"
            while url:
                try:
                    page = await self._get_json(url)
                except DocumentStoreError as exc:
                    if folder == "" and exc.status_code == 404:
                        return []  # the LaneReach folder does not exist yet
                    raise
                for raw in page.get("value", []):
                    relative = f"{folder}/{raw['name']}".strip("/")
                    if "folder" in raw:
                        pending.append(relative)
                    elif "file" in raw:
                        items.append(self._to_item(raw, relative))
                url = page.get("@odata.nextLink")
        return sorted(items, key=lambda item: item.path)

    async def download(self, item: DocumentItem) -> bytes:
        """Download a file (Graph redirects to a pre-authenticated URL)."""
        drive = await self._drive()
        response = await self._request(
            "GET",
            f"{self._settings.graph_base_url}/drives/{drive}/items/{quote(item.item_id)}/content",
            follow_redirects=True,
        )
        return response.content

    async def upload(self, path: str, content: bytes, content_type: str) -> DocumentItem:
        """Create or replace a file (simple upload, up to 4 MB)."""
        if len(content) > _MAX_SIMPLE_UPLOAD:
            raise DocumentStoreError("files larger than 4 MB cannot be uploaded to SharePoint yet")
        drive = await self._drive()
        full_path = quote(f"{self._root}/{clean_library_path(path)}")
        response = await self._request(
            "PUT",
            f"{self._settings.graph_base_url}/drives/{drive}/root:/{full_path}:/content",
            content=content,
            headers={"Content-Type": content_type},
        )
        return self._to_item(response.json(), clean_library_path(path))

    async def check_connection(self) -> str:
        """Authenticate and resolve the library; return its web URL (settings screen test)."""
        drive = await self._drive()
        page = await self._get_json(f"{self._settings.graph_base_url}/drives/{drive}")
        return str(page.get("webUrl", ""))

    # --- internals -----------------------------------------------------------------------

    async def _access_token(self) -> str:
        now = self._clock.now()
        if self._token and self._token_expires_at and now < self._token_expires_at:
            return self._token
        settings = self._settings
        try:
            response = await self._http.post(
                f"{settings.login_base_url}/{quote(settings.azure_tenant_id)}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": settings.client_id,
                    "client_secret": settings.client_secret,
                    "scope": GRAPH_SCOPE,
                },
            )
        except httpx.HTTPError as exc:
            raise DocumentStoreError(
                f"cannot reach Microsoft sign-in: {type(exc).__name__}"
            ) from exc
        if response.status_code != 200:
            raise DocumentStoreError(
                f"Microsoft sign-in refused the app ({response.status_code}): "
                f"{_error_code(response)}"
            )
        body = response.json()
        self._token = str(body["access_token"])
        self._token_expires_at = (
            now + timedelta(seconds=int(body.get("expires_in", 3600))) - _TOKEN_REFRESH_MARGIN
        )
        return self._token

    async def _drive(self) -> str:
        if self._drive_id:
            return self._drive_id
        parts = urlsplit(self._settings.site_url)
        if not parts.hostname:
            raise DocumentStoreError("the SharePoint site URL is not valid")
        site_path = parts.path.rstrip("/") or "/"
        site = await self._get_json(
            f"{self._settings.graph_base_url}/sites/{parts.hostname}:{quote(site_path)}"
        )
        drives = await self._get_json(f"{self._settings.graph_base_url}/sites/{site['id']}/drives")
        wanted = self._settings.library_name.strip().lower()
        for drive in drives.get("value", []):
            if str(drive.get("name", "")).lower() == wanted:
                self._drive_id = str(drive["id"])
                return self._drive_id
        raise DocumentStoreError(f"library {self._settings.library_name!r} not found on the site")

    async def _get_json(self, url: str) -> dict[str, Any]:
        response = await self._request("GET", url)
        payload: dict[str, Any] = response.json()
        return payload

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = {"Authorization": f"Bearer {await self._access_token()}"}
        headers.update(kwargs.pop("headers", {}))
        kwargs.setdefault("timeout", 30.0)  # documents are larger than the default budget
        try:
            response = await self._http.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise DocumentStoreError(f"cannot reach SharePoint: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            logger.warning("sharepoint_request_failed", status=response.status_code)
            raise DocumentStoreError(
                f"SharePoint answered {response.status_code}: {_error_code(response)}",
                status_code=response.status_code,
            )
        return response

    def _to_item(self, raw: dict[str, Any], relative_path: str) -> DocumentItem:
        return DocumentItem(
            item_id=str(raw["id"]),
            path=relative_path,
            etag=str(raw.get("cTag") or raw.get("eTag") or ""),
            modified_at=datetime.fromisoformat(
                str(raw.get("lastModifiedDateTime", "1970-01-01T00:00:00Z")).replace("Z", "+00:00")
            ),
            size=int(raw.get("size", 0)),
            web_url=raw.get("webUrl"),
        )


def _error_code(response: httpx.Response) -> str:
    """The Graph/AAD error code only (never the full body, which may echo request details)."""
    try:
        body = response.json()
    except ValueError:
        return "no details"
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("code", "unknown"))
    return str(error or body.get("error_codes") or "unknown")
