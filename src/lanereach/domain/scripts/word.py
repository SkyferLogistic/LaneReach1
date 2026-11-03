"""Write Word scripts (new scripts from the console, AI translations) for the SharePoint library."""

from __future__ import annotations

import io
import re

import docx

from lanereach.domain.scripts.importer import WORD_PLACEHOLDERS

_CANONICAL_TO_WORD = {canonical: word for word, canonical in WORD_PLACEHOLDERS.items()}
_CANONICAL_TO_WORD["company.name"] = "company_name"
_CANONICAL_TO_WORD["country.name"] = "country_name"
_CANONICAL = re.compile(r"\{\{\s*([a-z_]+(?:\.[a-z_]+)?)\s*(?:\|([^{}]*))?\}\}")
# `[[unsubscribe]]text[[/unsubscribe]]` marks the linked words (see `html_to_text`); plain
# "text ({{unsubscribe.url}})" falls back to the last three words before the link.
_UNSUBSCRIBE_MARKED = re.compile(r"\[\[unsubscribe\]\](.*?)\[\[/unsubscribe\]\]", re.DOTALL)
_UNSUBSCRIBE_TEXT = re.compile(r"(\S+(?: \S+){0,2}) \(\{\{\s*unsubscribe\.url\s*\}\}\)")


def canonical_to_word(text: str) -> str:
    """Rewrite LaneReach `{{…}}` placeholders in the Word single-brace syntax.

    Args:
        text: Plain-text template body or subject (as stored, or as translated by the AI).

    Returns:
        Text a marketer can edit in Word: `{first_name}`, `{banner}`, `{signature}`,
        `{unsubscribe_link|…}`.
    """
    text = _UNSUBSCRIBE_MARKED.sub(
        lambda match: "{unsubscribe_link|" + match.group(1).strip() + "}", text
    )
    text = _UNSUBSCRIBE_TEXT.sub(
        lambda match: "{unsubscribe_link|" + match.group(1).strip() + "}", text
    )

    def replace(match: re.Match[str]) -> str:
        name, fallback = match.group(1), match.group(2)
        if name == "banner.html":
            return "{banner}"
        if name in {"sender.signature", "sender.signature_text"}:
            return "{signature}"
        word = _CANONICAL_TO_WORD.get(name)
        if word is None:
            return match.group(0)
        return "{" + word + (f"|{fallback}" if fallback else "") + "}"

    return _CANONICAL.sub(replace, text)


def build_word_script(subject: str, body: str) -> bytes:
    """Create a `.docx` script: a `Subject:` line, then one paragraph per line of `body`.

    Args:
        subject: Subject line (Word or canonical placeholders).
        body: Body text; blank lines separate paragraphs.

    Returns:
        The Word document.
    """
    document = docx.Document()
    style = document.styles["Normal"]
    style.font.name = "Calibri"
    document.add_paragraph(f"Subject: {canonical_to_word(subject).strip()}")
    for paragraph in re.split(r"\n\s*\n", canonical_to_word(body).strip()):
        lines = paragraph.split("\n")
        added = document.add_paragraph(lines[0].strip())
        for line in lines[1:]:
            added.add_run().add_break()
            added.add_run(line.strip())
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
