"""The document library interface, and the local-folder library used in development and tests.

Paths are relative to the library's LaneReach root folder and always use forward slashes,
for example `Scripts/Air/USA-Canada to country/tr.docx` or `Banners/Air/TR.png`.
"""

from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Protocol

from lanereach.core.errors import BusinessRuleViolationError


class DocumentStoreError(Exception):
    """The library could not be reached or refused the operation.

    Args:
        message: What went wrong (safe to show; never contains secrets).
        status_code: HTTP status from the library, when there was one.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class DocumentItem:
    """A file in the library."""

    item_id: str
    path: str
    etag: str
    modified_at: datetime
    size: int
    web_url: str | None = None

    @property
    def name(self) -> str:
        """File name with extension."""
        return PurePosixPath(self.path).name

    @property
    def extension(self) -> str:
        """Lowercase extension without the dot (`docx`, `pdf`, `png`)."""
        return PurePosixPath(self.path).suffix.lower().lstrip(".")


class DocumentStore(Protocol):
    """A document library LaneReach reads scripts and banners from and writes new ones to."""

    kind: str

    async def list_files(self) -> list[DocumentItem]:
        """Every file under the LaneReach root folder (recursively)."""
        ...

    async def download(self, item: DocumentItem) -> bytes:
        """The file's content."""
        ...

    async def upload(self, path: str, content: bytes, content_type: str) -> DocumentItem:
        """Create or replace a file."""
        ...


def clean_library_path(path: str) -> str:
    """Normalise a relative library path and refuse anything that escapes the root.

    Args:
        path: `Scripts/Air/Name/tr.docx`.

    Returns:
        The normalised path.

    Raises:
        BusinessRuleViolationError: For absolute paths, `..` segments or empty names.
    """
    parts = [part.strip() for part in path.replace("\\", "/").split("/") if part.strip()]
    if not parts or any(part in {".", ".."} for part in parts) or path.startswith("/"):
        raise BusinessRuleViolationError(f"Invalid library path: {path!r}")
    forbidden = set('<>:"|?*')
    if any(forbidden & set(part) for part in parts):
        raise BusinessRuleViolationError(f"Invalid characters in library path: {path!r}")
    return "/".join(parts)


class LocalDocumentStore:
    """A folder on disk laid out like the SharePoint library (one folder per tenant).

    Args:
        root: The tenant's folder.
    """

    kind = "local"

    def __init__(self, root: Path) -> None:
        self._root = root

    async def list_files(self) -> list[DocumentItem]:
        """Every file under the folder."""
        if not self._root.exists():
            return []
        items = []
        for file in sorted(self._root.rglob("*")):
            if not file.is_file() or file.name.startswith(("~$", ".")):
                continue
            items.append(self._item(file))
        return items

    async def download(self, item: DocumentItem) -> bytes:
        """Read a file."""
        target = self._resolve(item.path)
        if not target.is_file():
            raise DocumentStoreError(f"{item.path} no longer exists")
        return target.read_bytes()

    async def upload(self, path: str, content: bytes, content_type: str) -> DocumentItem:
        """Write a file (creating folders)."""
        target = self._resolve(clean_library_path(path))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return self._item(target)

    def _resolve(self, relative: str) -> Path:
        target = (self._root / relative).resolve()
        if self._root.resolve() not in target.parents and target != self._root.resolve():
            raise BusinessRuleViolationError(f"Invalid library path: {relative!r}")
        return target

    def _item(self, file: Path) -> DocumentItem:
        content = file.read_bytes()
        stat = file.stat()
        relative = file.relative_to(self._root).as_posix()
        return DocumentItem(
            item_id=f"local:{relative}",
            path=relative,
            etag=hashlib.sha256(content).hexdigest()[:32],
            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            size=stat.st_size,
            web_url=None,
        )


def guess_content_type(name: str) -> str:
    """Content type from a file name (`application/octet-stream` when unknown)."""
    if name.lower().endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return mimetypes.guess_type(name)[0] or "application/octet-stream"
