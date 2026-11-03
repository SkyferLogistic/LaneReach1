"""Reusable, validated field types for node config models."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

TemplateKey = Annotated[
    str,
    Field(
        pattern=r"^[a-z][a-z0-9_]{1,63}$",
        description="Key of an email template, for example `intro` or `follow_up_1`.",
        examples=["intro"],
    ),
]

TagName = Annotated[
    str,
    Field(
        min_length=1,
        max_length=50,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9 _:-]*$",
        description="Contact tag.",
        examples=["uae-importers"],
    ),
]

HandleName = Annotated[
    str,
    Field(
        pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$",
        description="Lowercase name used as an output handle.",
        examples=["variant_a"],
    ),
]

LanguageCode = Annotated[
    str,
    Field(pattern=r"^[a-z]{2}$", description="ISO 639-1 language code.", examples=["fr"]),
]

TemplateLanguage = Annotated[
    str,
    Field(
        pattern=r"^[a-z]{2}(-[a-z0-9]{2,4})?$",
        description="Template language: ISO 639-1, optionally with a variant (pt-br, zh-hant).",
        examples=["fr", "pt-br"],
    ),
]

RegionCode = Annotated[
    str,
    Field(pattern=r"^[A-Z]{2}$", description="ISO 3166-1 alpha-2 country code.", examples=["AE"]),
]

NotificationText = Annotated[
    str,
    Field(
        min_length=1,
        max_length=1000,
        description="Message text. Placeholders such as `{{contact.first_name}}` are filled in.",
    ),
]
