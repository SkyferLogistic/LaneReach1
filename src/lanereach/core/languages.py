"""Template language variants (`en`, `pt-br`, `zh-hant`, `en-na`...) and their names.

A template language is an ISO 639-1 code, optionally followed by a variant: a region (`pt-br`,
`pt-pt`), a script (`zh-hans`, `zh-hant`) or a market (`en-na`, English written for North
America). Contacts and offices keep plain ISO 639-1 codes; the destination-country table says
which variant each country receives.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

LANGUAGE_VARIANT_PATTERN = r"^[a-z]{2}(-[a-z0-9]{2,4})?$"
_VARIANT = re.compile(LANGUAGE_VARIANT_PATTERN)


@dataclass(frozen=True, slots=True)
class LanguageVariant:
    """How a template language is named and translated."""

    code: str
    name: str
    base: str
    region: str | None = None
    right_to_left: bool = False


KNOWN_VARIANTS: dict[str, LanguageVariant] = {
    variant.code: variant
    for variant in (
        LanguageVariant("en", "English", "en"),
        LanguageVariant("en-na", "English (North America)", "en", "US"),
        LanguageVariant("fr", "French", "fr", "FR"),
        LanguageVariant("de", "German", "de", "DE"),
        LanguageVariant("nl", "Dutch", "nl", "NL"),
        LanguageVariant("it", "Italian", "it", "IT"),
        LanguageVariant("es", "Spanish", "es"),
        LanguageVariant("pt-br", "Portuguese (Brazil)", "pt", "BR"),
        LanguageVariant("pt-pt", "Portuguese (Portugal)", "pt", "PT"),
        LanguageVariant("pl", "Polish", "pl", "PL"),
        LanguageVariant("tr", "Turkish", "tr", "TR"),
        LanguageVariant("ar", "Arabic", "ar", None, right_to_left=True),
        LanguageVariant("zh-hans", "Chinese (Simplified)", "zh", "CN"),
        LanguageVariant("zh-hant", "Chinese (Traditional)", "zh", "TW"),
        LanguageVariant("ko", "Korean", "ko", "KR"),
        LanguageVariant("vi", "Vietnamese", "vi", "VN"),
        LanguageVariant("th", "Thai", "th", "TH"),
        LanguageVariant("id", "Indonesian", "id", "ID"),
        LanguageVariant("pt", "Portuguese", "pt"),
        LanguageVariant("zh", "Chinese", "zh"),
        LanguageVariant("ja", "Japanese", "ja", "JP"),
        LanguageVariant("ru", "Russian", "ru", "RU"),
        LanguageVariant("hi", "Hindi", "hi", "IN"),
    )
}


def _simplify(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", folded.lower()).strip()


_BY_NAME = {_simplify(variant.name): code for code, variant in KNOWN_VARIANTS.items()}
_BY_NAME |= {
    "english na": "en-na",
    "english usa canada": "en-na",
    "english north america": "en-na",
    "portuguese br": "pt-br",
    "brazilian portuguese": "pt-br",
    "portuguese pt": "pt-pt",
    "simplified chinese": "zh-hans",
    "traditional chinese": "zh-hant",
    "chinese simplified": "zh-hans",
    "chinese traditional": "zh-hant",
}


def is_language_variant(code: str) -> bool:
    """Whether `code` is a well-formed template language (known or not).

    Args:
        code: Candidate code, for example `pt-br`.

    Returns:
        True when it matches the variant pattern.
    """
    return bool(_VARIANT.match(code))


def parse_language_variant(text: str) -> str | None:
    """Read a template language from a code or a name (a file name like `Turkish.docx`).

    Args:
        text: `tr`, `pt-BR`, `Portuguese (Brazil)`, `Chinese - Traditional`...

    Returns:
        The normalised code, or None if it is not recognisable.
    """
    candidate = text.strip().lower().replace("_", "-")
    if is_language_variant(candidate):
        return candidate
    return _BY_NAME.get(_simplify(text))


def split_language_variant(code: str) -> tuple[str, str | None]:
    """ISO 639-1 base language and ISO 3166 region of a variant (for translation prompts).

    Args:
        code: Template language, for example `zh-hant`.

    Returns:
        `("zh", "TW")`; unknown variants give their base language and no region.
    """
    known = KNOWN_VARIANTS.get(code)
    if known is not None:
        return known.base, known.region
    base, _, region = code.partition("-")
    return base, region.upper() if len(region) == 2 else None


def language_name(code: str) -> str:
    """Display name of a template language (`pt-br` -> `Portuguese (Brazil)`)."""
    known = KNOWN_VARIANTS.get(code)
    return known.name if known else code


def is_right_to_left(code: str) -> bool:
    """Whether emails in this language are written right to left (Arabic, Hebrew...)."""
    return split_language_variant(code)[0] in {"ar", "he", "fa", "ur"}
