"""Turn a Word script into an email template (HTML plus a plain-text alternative).

Marketing writes scripts in Word with simple placeholders in single braces::

    Subject: Air freight from the USA or Canada {to_country}
    Hi {first_name},
    ... enquiries from the USA or Canada {to_country}.
    {banner}
    We look forward to quoting your next shipment.
    Best regards,
    {signature}
    You're receiving this email as a fellow {network} member.
    Prefer not to receive these emails? {unsubscribe_link|Unsubscribe here}.

The importer converts the document with mammoth (clean semantic HTML, embedded images kept),
maps the placeholders to LaneReach's `{{scope.field}}` syntax, sanitises the result (the document
is untrusted input), and lays it out for email clients. It is pure: images are returned for the
caller to store, and referenced as `lr-image://<index>` until then.
"""

from __future__ import annotations

import html
import io
import re
from dataclasses import dataclass, field
from typing import Any

import mammoth
import nh3

from lanereach.core.languages import is_right_to_left


class ScriptImportError(ValueError):
    """The document cannot be used as a script (not a Word file, no subject...)."""


@dataclass(frozen=True, slots=True)
class ExtractedImage:
    """An image embedded in the Word document."""

    content: bytes
    content_type: str
    alt: str


@dataclass
class ImportedScript:
    """The converted script."""

    subject: str
    html_body: str
    text_body: str
    images: list[ExtractedImage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# Word placeholder -> LaneReach placeholder name.
WORD_PLACEHOLDERS: dict[str, str] = {
    "first_name": "contact.first_name",
    "last_name": "contact.last_name",
    "full_name": "contact.full_name",
    "job_title": "contact.job_title",
    "company_name": "company.name",
    "company": "company.name",
    "country_name": "country.name",
    "country": "country.name",
    "to_country": "country.to_phrase",
    "network": "contact.network",
    "agent_name": "sender.name",
    "agent_first_name": "sender.first_name",
    "agent_title": "sender.title",
    "agent_email": "sender.email",
    "agent_phone": "sender.phone",
    "office_name": "office.name",
    "unsubscribe_url": "unsubscribe.url",
}
SPECIAL_PLACEHOLDERS = ("banner", "signature", "unsubscribe_link")

# "Subject:" in every script language, so each language's Word file can carry its own subject.
_SUBJECT_LABELS = (
    "subject line",
    "subject",
    "objet",
    "asunto",
    "betreff",
    "onderwerp",
    "oggetto",
    "assunto",
    "temat",
    "konu",
    "الموضوع",
    "主题",
    "主旨",
    "제목",
    "tiêu đề",
    "หัวข้อ",
    "subjek",
    "perihal",
)
_SUBJECT = re.compile(
    r"^\s*(?:"
    + "|".join(re.escape(label) for label in _SUBJECT_LABELS)
    + r")\s*[:：]\s*(?P<s>.+)$",  # noqa: RUF001 - fullwidth colon of CJK subjects
    re.IGNORECASE,
)
_TAG = re.compile(r"<[^>]+>")
_BRACED = re.compile(r"\{([^{}]{1,160})\}")
_WORD_TOKEN = re.compile(r"^\s*([A-Za-z][A-Za-z _-]{0,40}?)\s*(?:\|(.*))?$", re.DOTALL)
_FIRST_PARAGRAPH = re.compile(r"^\s*<p>(.*?)</p>", re.DOTALL)
_ENCODED_BRACES = re.compile(r"%7B\s*([a-z_]+)\s*%7D", re.IGNORECASE)

_ALLOWED_TAGS = {
    "p",
    "br",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "s",
    "sub",
    "sup",
    "span",
    "a",
    "img",
    "h1",
    "h2",
    "h3",
    "h4",
    "ul",
    "ol",
    "li",
    "table",
    "thead",
    "tbody",
    "tr",
    "td",
    "th",
    "blockquote",
    "hr",
    "div",
}
_ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"src", "alt", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}
_URL_SCHEMES = {"http", "https", "mailto", "tel", "lr-image", "lr-asset"}

_STYLES = {
    "p": "margin:0 0 12px 0;",
    "h1": "margin:0 0 12px 0;font-size:22px;",
    "h2": "margin:0 0 12px 0;font-size:18px;",
    "h3": "margin:0 0 10px 0;font-size:16px;",
    "a": "color:#1d5fbf;",
    "img": "max-width:100%;height:auto;border:0;",
    "table": "border-collapse:collapse;",
    "td": "padding:4px 8px;vertical-align:top;",
    "ul": "margin:0 0 12px 20px;padding:0;",
    "ol": "margin:0 0 12px 20px;padding:0;",
}


def _normalise_name(raw: str) -> str:
    return re.sub(r"[\s-]+", "_", raw.strip().lower())


def _text_of(fragment: str) -> str:
    return html.unescape(_TAG.sub("", fragment)).strip()


def convert_word_script(content: bytes, *, language: str) -> ImportedScript:
    """Convert a `.docx` script into HTML and text with LaneReach placeholders.

    Args:
        content: The Word document.
        language: Template language (sets right-to-left layout for Arabic).

    Returns:
        The converted script; `images` must be stored and their `lr-image://<i>` references
        replaced by the caller.

    Raises:
        ScriptImportError: If the file is not a readable Word document or has no subject.
    """
    images: list[ExtractedImage] = []

    def keep_image(image: Any) -> dict[str, str]:
        with image.open() as stream:
            data = stream.read()
        images.append(ExtractedImage(data, image.content_type or "image/png", image.alt_text or ""))
        return {"src": f"lr-image://{len(images) - 1}", "alt": image.alt_text or ""}

    try:
        result = mammoth.convert_to_html(
            io.BytesIO(content), convert_image=mammoth.images.img_element(keep_image)
        )
    except Exception as exc:  # mammoth raises zipfile/KeyError/... for broken files
        raise ScriptImportError("this is not a readable Word (.docx) document") from exc

    warnings: list[str] = []
    body = str(result.value)
    subject, body = _take_subject(body)
    if not subject:
        raise ScriptImportError(
            "add the subject as the first line of the document, e.g. 'Subject: Air freight rates'"
        )
    body = _ENCODED_BRACES.sub(lambda match: "{" + match.group(1) + "}", body)
    body, found = _convert_placeholders(body, warnings)
    subject, _ = _convert_placeholders(html.escape(subject, quote=False), warnings)
    subject = html.unescape(subject)

    if "signature" not in found:
        warnings.append("No {signature} placeholder: the agent's signature was added at the end.")
        body += "{{sender.signature}}"
    if "unsubscribe_link" not in found and "unsubscribe_url" not in found:
        warnings.append(
            "No {unsubscribe_link}: an English unsubscribe line was added. "
            "Add {unsubscribe_link|…} in the script's language."
        )
        body += '<p style="font-size:12px;color:#6b7280;">'
        body += '<a href="{{unsubscribe.url}}">Unsubscribe</a></p>'
    if images and "banner" not in found:
        warnings.append(
            f"{len(images)} image(s) are embedded and go to every country. "
            "Use {banner} where the country banner should appear."
        )

    sanitized = nh3.clean(
        body,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes=_URL_SCHEMES,
        link_rel=None,
    )
    styled = _apply_styles(sanitized)
    direction = ' dir="rtl"' if is_right_to_left(language) else ""
    align = "right" if direction else "left"
    html_body = (
        f'<div{direction} style="font-family:Calibri,Arial,Helvetica,sans-serif;font-size:14px;'
        f'line-height:1.5;color:#1f2933;max-width:640px;text-align:{align};">{styled}</div>'
    )
    return ImportedScript(
        subject=subject.strip(),
        html_body=html_body,
        text_body=html_to_text(sanitized),
        images=images,
        warnings=warnings + [str(message.message) for message in result.messages][:5],
    )


def _take_subject(body: str) -> tuple[str | None, str]:
    match = _FIRST_PARAGRAPH.match(body)
    if match:
        found = _SUBJECT.match(_text_of(match.group(1)))
        if found:
            return found.group("s").strip(), body[match.end() :]
    return None, body


def _convert_placeholders(fragment: str, warnings: list[str]) -> tuple[str, set[str]]:
    """Replace Word `{placeholders}` (even when Word split them across formatting runs)."""
    found: set[str] = set()

    def block(name: str, replacement: str, text: str) -> str:
        # A placeholder alone in its paragraph replaces the whole paragraph (no empty <p>).
        return re.sub(
            r"<p>\s*(?:<[^>]+>\s*)*\{\s*" + name + r"\s*\}\s*(?:<[^>]+>\s*)*</p>",
            replacement,
            text,
            flags=re.IGNORECASE,
        )

    def substitute(match: re.Match[str]) -> str:
        replacement = convert(match)
        if replacement == match.group(0):
            return replacement
        # Word may split "{first_name}" across formatting runs ("{<b>first</b>_name}"): keep the
        # tags that were inside the braces so the surrounding markup stays balanced.
        return replacement + "".join(_TAG.findall(match.group(1)))

    def convert(match: re.Match[str]) -> str:
        inner = _text_of(match.group(1))
        token = _WORD_TOKEN.match(inner)
        if not token:
            return match.group(0)
        name, fallback = _normalise_name(token.group(1)), token.group(2)
        if name == "banner":
            found.add(name)
            return "{{banner.html|}}"
        if name == "signature":
            found.add(name)
            return "{{sender.signature}}"
        if name == "unsubscribe_link":
            found.add(name)
            label = html.escape((fallback or "Unsubscribe").strip(), quote=False)
            return f'<a href="{{{{unsubscribe.url}}}}">{label}</a>'
        target = WORD_PLACEHOLDERS.get(name)
        if target is None:
            warnings.append(f"Unknown placeholder {{{inner}}} was left as written.")
            return match.group(0)
        found.add(name)
        if fallback is not None:
            safe = html.escape(fallback.strip().replace("}", ""), quote=False)
            return "{{" + target + "|" + safe + "}}"
        return "{{" + target + "}}"

    fragment = block("banner", "{{banner.html|}}", fragment)
    fragment = block("signature", "{{sender.signature}}", fragment)
    if "{{banner.html|}}" in fragment:
        found.add("banner")
    if "{{sender.signature}}" in fragment:
        found.add("signature")
    # Keep already-canonical {{...}} placeholders untouched: only single-brace tokens match.
    converted = re.sub(r"(?<!\{)\{([^{}]{1,160})\}(?!\})", substitute, fragment)
    return converted, found


_STYLED_TAG = re.compile(r"<(p|h1|h2|h3|a|img|table|td|ul|ol)(\s[^>]*)?>")


def _apply_styles(fragment: str) -> str:
    """Inline styles for email clients (they ignore <style> blocks); sanitising removed any."""

    def add_style(match: re.Match[str]) -> str:
        tag, rest = match.group(1), match.group(2) or ""
        return f'<{tag}{rest} style="{_STYLES[tag]}">'

    return _STYLED_TAG.sub(add_style, fragment)


def html_to_text(fragment: str, *, keep_blocks: bool = False) -> str:
    """Plain-text alternative of an HTML email (placeholders kept, special blocks adapted).

    Args:
        fragment: Sanitised HTML.
        keep_blocks: Keep `{{banner.html|}}` and `{{sender.signature}}` on their own lines
            (the source text for AI translation, so the Word translation keeps them).

    Returns:
        Text with paragraphs separated by blank lines and links written as `text (url)`.
    """
    if keep_blocks:
        text = fragment.replace("{{banner.html|}}", "\n\n{{banner.html|}}\n\n").replace(
            "{{sender.signature}}", "\n\n{{sender.signature}}\n\n"
        )
        # Mark exactly which words carry the unsubscribe link, so a translation keeps it.
        text = re.sub(
            r'<a\s[^>]*href="\{\{unsubscribe\.url\}\}"[^>]*>(.*?)</a>',
            r"[[unsubscribe]]\1[[/unsubscribe]]",
            text,
            flags=re.DOTALL,
        )
    else:
        text = fragment.replace("{{banner.html|}}", "").replace(
            "{{sender.signature}}", "\n\n{{sender.signature_text}}\n\n"
        )
    text = re.sub(r'<a\s[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r"\2 (\1)", text, flags=re.DOTALL)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"</(p|h1|h2|h3|li|tr|div|blockquote)>", "\n\n", text)
    text = re.sub(r"<li[^>]*>", "- ", text)
    text = re.sub(r"<img[^>]*>", "", text)
    text = html.unescape(_TAG.sub("", text))
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
