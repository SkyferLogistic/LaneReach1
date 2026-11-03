"""Placeholder rendering for email templates (no code execution, no Jinja).

Syntax: `{{scope.field}}` with an optional fallback, `{{contact.first_name|there}}`. Only the
variables the caller passes can be used; an unknown or empty placeholder without fallback is an
error, so a half-filled email ("Dear ,") is never sent.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping

PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+(?:\.[a-z_]+)?)\s*(?:\|([^{}]*))?\}\}")


class SafeHtml(str):
    """HTML built by LaneReach itself (signature block, banner), inserted without escaping.

    Every other value placed into an HTML body is escaped, so a contact named `<b>x</b>` or a
    company name with `&` cannot change the email's markup.
    """

    __slots__ = ()


class TemplateRenderError(ValueError):
    """A placeholder had no value and no fallback."""

    def __init__(self, missing: list[str]) -> None:
        super().__init__("missing template values: " + ", ".join(missing))
        self.missing = missing


def find_placeholders(text: str) -> list[str]:
    """Return the placeholder names in `text`, in order, without duplicates.

    Args:
        text: Template subject or body.

    Returns:
        Names such as `contact.first_name`.
    """
    seen: dict[str, None] = {}
    for match in PLACEHOLDER.finditer(text):
        seen.setdefault(match.group(1), None)
    return list(seen)


def render_template_text(
    text: str,
    values: Mapping[str, str | None],
    *,
    html_mode: bool = False,
    lenient: bool = False,
) -> str:
    """Replace placeholders with values.

    Args:
        text: Template subject or body.
        values: Available values keyed by placeholder name.
        html_mode: The text is HTML: escape values unless they are `SafeHtml`.
        lenient: Show a missing value as `[name]` instead of failing (previews only).

    Returns:
        The rendered text.

    Raises:
        TemplateRenderError: If a placeholder has neither a value nor a fallback.
    """
    missing: list[str] = []

    def emit(value: str) -> str:
        if html_mode and not isinstance(value, SafeHtml):
            return html.escape(value, quote=True)
        return value

    def substitute(match: re.Match[str]) -> str:
        name, fallback = match.group(1), match.group(2)
        value = values.get(name)
        if value:
            return emit(value)
        if fallback is not None:
            return emit(fallback.strip())
        if lenient:
            return emit(f"[{name}]")
        missing.append(name)
        return ""

    rendered = PLACEHOLDER.sub(substitute, text)
    if missing:
        raise TemplateRenderError(sorted(set(missing)))
    return rendered
