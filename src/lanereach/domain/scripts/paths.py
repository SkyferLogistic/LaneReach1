"""Library layout: what a file path in the scripts library means.

```
LaneReach/
  Scripts/Air/<Script name>/<language>.docx     one Word file per language (+ optional .pdf)
  Scripts/Ocean/<Script name>/<language>.docx
  Scripts/Road|Rail|Courier|Customs|Warehousing|Project|General/...  the other service lines
  Banners/Air/<CC or country name>.png, Banners/Air/default.png   banner for {banner}
  Banners/Ocean/<CC>.png
  Brand/logo.png  Brand/badge-*.png            images for generated signatures
  Rates/<any folders>/<sheet>.xlsx|.csv          pricing rate sheets (imported by pricing)
```
Language files may be named by code (`tr.docx`, `pt-br.docx`) or by name (`Turkish.docx`,
`Portuguese (Brazil).docx`).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath

from lanereach.core.languages import parse_language_variant
from lanereach.domain.senders.models import ServiceLine

SCRIPT_EXTENSIONS = {"docx", "pdf"}
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
RATE_EXTENSIONS = {"xlsx", "xlsm", "csv"}
LINE_FOLDERS = {
    ServiceLine.AIR: "Air",
    ServiceLine.OCEAN: "Ocean",
    ServiceLine.ROAD: "Road",
    ServiceLine.RAIL: "Rail",
    ServiceLine.COURIER: "Courier",
    ServiceLine.CUSTOMS: "Customs",
    ServiceLine.WAREHOUSING: "Warehousing",
    ServiceLine.PROJECT: "Project",
    ServiceLine.ANY: "General",
}
_FOLDER_LINES = {folder.lower(): line for line, folder in LINE_FOLDERS.items()}


@dataclass(frozen=True, slots=True)
class ScriptFile:
    """A script document (`Scripts/<Line>/<Name>/<language>.<ext>`)."""

    service_line: ServiceLine
    script_name: str
    language: str
    extension: str

    @property
    def key(self) -> str:
        """Template key: service line + slug of the script name."""
        return script_key(self.service_line, self.script_name)


@dataclass(frozen=True, slots=True)
class BannerFile:
    """A banner image (`Banners/<Line>/<CC, country name or default>.<ext>`)."""

    service_line: ServiceLine
    country: str | None


@dataclass(frozen=True, slots=True)
class BrandFile:
    """A brand image (`Brand/<name>.<ext>`)."""

    name: str


@dataclass(frozen=True, slots=True)
class RateSheetFile:
    """A pricing rate sheet (`Rates/<folders>/<name>.xlsx|csv`), named by its path under Rates."""

    name: str


def script_key(service_line: ServiceLine, script_name: str) -> str:
    """Stable template key such as `air_usa_canada_to_country` (at most 64 characters).

    Args:
        service_line: Air, ocean or any.
        script_name: Folder name of the script.

    Returns:
        The key.
    """
    folded = unicodedata.normalize("NFKD", script_name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "_", folded.lower()).strip("_") or "script"
    return f"{service_line.value}_{slug}"[:64].rstrip("_")


def script_path(service_line: ServiceLine, script_name: str, language: str, extension: str) -> str:
    """Library path of a script document."""
    return f"Scripts/{LINE_FOLDERS[service_line]}/{script_name.strip()}/{language}.{extension}"


def banner_path(service_line: ServiceLine, country: str | None, extension: str) -> str:
    """Library path of a banner image."""
    return f"Banners/{LINE_FOLDERS[service_line]}/{country or 'default'}.{extension}"


def parse_library_path(
    path: str,
) -> ScriptFile | BannerFile | BrandFile | RateSheetFile | None:
    """Understand a library path; None for files LaneReach does not use.

    Args:
        path: Path relative to the LaneReach folder.

    Returns:
        What the file is.
    """
    parts = PurePosixPath(path).parts
    suffix = PurePosixPath(path).suffix.lower().lstrip(".")
    stem = PurePosixPath(path).stem
    if len(parts) == 4 and parts[0].lower() == "scripts" and suffix in SCRIPT_EXTENSIONS:
        line = _FOLDER_LINES.get(parts[1].lower())
        language = parse_language_variant(stem)
        if line is None or language is None:
            return None
        return ScriptFile(line, parts[2], language, suffix)
    if len(parts) == 3 and parts[0].lower() == "banners" and suffix in IMAGE_EXTENSIONS:
        line = _FOLDER_LINES.get(parts[1].lower())
        if line is None:
            return None
        if stem.lower() == "default":
            return BannerFile(line, None)
        # "TR" or "Turkey": names are resolved against the countries table during the sync.
        return BannerFile(line, stem.upper() if re.fullmatch(r"[A-Za-z]{2}", stem) else stem)
    if len(parts) == 2 and parts[0].lower() == "brand" and suffix in IMAGE_EXTENSIONS:
        return BrandFile(stem.lower())
    if len(parts) >= 2 and parts[0].lower() == "rates" and suffix in RATE_EXTENSIONS:
        # Excel's lock files (~$Rates.xlsx) appear while someone has the sheet open.
        if parts[-1].startswith("~$"):
            return None
        return RateSheetFile("/".join(parts[1:])[:300])
    return None
