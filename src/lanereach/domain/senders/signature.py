"""Agent email signatures: generated from branding + agent details, custom HTML, or an image.

Generated signatures are table-based with inline styles, the layout every mail client renders
the same way (Outlook ignores most CSS). Values are escaped; only LaneReach builds the markup.
"""

from __future__ import annotations

import html
import uuid
from collections.abc import Callable

import nh3

from lanereach.domain.senders.models import Sender, SignatureMode
from lanereach.domain.templates.rendering import SafeHtml
from lanereach.tenants.branding import TenantBranding

AssetUrl = Callable[[uuid.UUID], str]

_SIGNATURE_TAGS = {
    "table",
    "tbody",
    "tr",
    "td",
    "th",
    "p",
    "br",
    "span",
    "strong",
    "b",
    "em",
    "i",
    "a",
    "img",
    "div",
    "hr",
}
_SIGNATURE_ATTRIBUTES = {
    "a": {"href"},
    "img": {"src", "alt", "width", "height"},
    "td": {"colspan", "rowspan", "width", "valign", "align"},
    "table": {"width", "cellpadding", "cellspacing", "border"},
    "*": {"style"},
}


def clean_custom_signature(markup: str) -> str:
    """Sanitise an agent's custom HTML signature (scripts, handlers and odd URLs removed)."""
    return str(
        nh3.clean(
            markup,
            tags=_SIGNATURE_TAGS,
            attributes=_SIGNATURE_ATTRIBUTES,
            url_schemes={"http", "https", "mailto", "tel"},
            link_rel=None,
        )
    )


def _e(value: str | None) -> str:
    return html.escape(value or "", quote=True)


def build_signature_html(
    sender: Sender, branding: TenantBranding, company_name: str, asset_url: AssetUrl
) -> SafeHtml:
    """The agent's signature block as email-safe HTML.

    The generated layout follows the forwarder signatures it replaces: agent, title and QR
    code on the left; logo, tagline and offices in the middle; phone, WhatsApp, fax, email,
    address and website on the right; association badges underneath and an optional
    promotional banner (for example a warehouse offer) across the bottom.

    Args:
        sender: The agent.
        branding: Tenant branding.
        company_name: Tenant name (when branding has none).
        asset_url: Builds the public link of an image asset.

    Returns:
        Signature HTML, marked safe for template rendering.
    """
    if sender.signature_mode is SignatureMode.CUSTOM_HTML and sender.signature_html:
        return SafeHtml(clean_custom_signature(sender.signature_html))
    if sender.signature_mode is SignatureMode.IMAGE and sender.signature_asset_id:
        return SafeHtml(
            signature_image_html(asset_url(sender.signature_asset_id), sender.display_name)
        )
    primary, accent = branding.primary_color, branding.accent_color
    company = branding.company_name or company_name
    return SafeHtml(
        '<table cellpadding="0" cellspacing="0" border="0" width="600" '
        'style="width:600px;max-width:100%;border-collapse:collapse;margin-top:12px;'
        f'border:1px solid #e5e7eb;border-left:4px solid {accent};"><tr>'
        '<td width="33%" valign="top" style="padding:12px 14px;border-right:1px solid #e5e7eb;">'
        f"{_agent_column(sender, branding, asset_url)}</td>"
        '<td width="31%" valign="middle" align="center" style="padding:12px 10px;'
        'border-right:1px solid #e5e7eb;text-align:center;">'
        f"{_brand_column(branding, company, asset_url)}</td>"
        '<td valign="middle" style="padding:10px 12px;">'
        '<table cellpadding="0" cellspacing="0" border="0">'
        f"{_contact_rows(sender, branding, primary)}</table></td>"
        f"</tr>{_badge_row(branding, asset_url)}{_promo_row(branding, company, asset_url)}</table>"
    )


def signature_image_html(url: str, alt: str) -> str:
    """A signature picture (PNG/JPEG) as email HTML."""
    return (
        f'<p style="margin:12px 0 0 0;"><img src="{_e(url)}" alt="{_e(alt)}" width="600" '
        'style="display:block;max-width:600px;width:100%;height:auto;border:0;"></p>'
    )


_FONT = "font-family:Arial,Helvetica,sans-serif;"


def _block(style: str, content: str) -> str:
    return f'<div style="{_FONT}{style}">{content}</div>'


def _link(href: str, text: str) -> str:
    return f'<a href="{_e(href)}" style="color:#1f2933;text-decoration:none;">{_e(text)}</a>'


def _agent_column(sender: Sender, branding: TenantBranding, asset_url: AssetUrl) -> str:
    primary, accent = branding.primary_color, branding.accent_color
    qr = (
        f'<img src="{_e(asset_url(branding.qr_asset_id))}" alt="QR code" width="86" height="86" '
        'style="display:block;width:86px;height:86px;border:0;margin:10px auto 0 auto;">'
        if branding.qr_asset_id
        else ""
    )
    return (
        _block(f"font-size:17px;font-weight:bold;color:{primary};", _e(sender.display_name))
        + _block(f"font-size:13px;color:{primary};", _e(sender.job_title))
        + f'<div style="height:4px;margin-top:8px;background:{accent};width:100%;"></div>'
        + qr
    )


def _brand_column(branding: TenantBranding, company: str, asset_url: AssetUrl) -> str:
    primary = branding.primary_color
    if branding.logo_asset_id:
        logo = (
            f'<img src="{_e(asset_url(branding.logo_asset_id))}" alt="{_e(company)}" width="150" '
            'style="display:block;border:0;max-width:150px;height:auto;margin:0 auto 6px auto;">'
        )
    else:
        logo = _block(f"font-size:20px;font-weight:bold;color:{primary};", _e(company))
    tagline = (
        _block(f"font-size:13px;color:{primary};font-weight:bold;", _e(branding.tagline))
        if branding.tagline
        else ""
    )
    offices = (
        _block("font-size:12px;color:#1f2933;margin-top:8px;", _e(branding.offices_strip))
        if branding.offices_strip
        else ""
    )
    return logo + tagline + offices


def _contact_rows(sender: Sender, branding: TenantBranding, primary: str) -> str:
    rows: list[tuple[str, str]] = []
    phone = sender.phone or branding.phone
    if phone:
        rows.append(("&#9990;", _link(f"tel:{_dial(phone)}", phone)))
    if sender.mobile:
        # WhatsApp opens a chat with the digits of the number.
        rows.append(("&#128241;", _link(f"https://wa.me/{_digits(sender.mobile)}", sender.mobile)))
    if branding.fax:
        rows.append(("&#128224;", _e(branding.fax)))
    rows.append(("&#9993;", _link(f"mailto:{sender.email}", sender.email)))
    if branding.address:
        rows.append(("&#9679;", _e(branding.address)))
    if branding.website:
        rows.append(("&#127760;", _link(_absolute(branding.website), branding.website)))
    return "".join(
        f'<tr><td style="padding:2px 6px 2px 0;color:{primary};{_FONT}font-size:13px;" '
        f'valign="top">{icon}</td>'
        f'<td style="padding:2px 0;{_FONT}font-size:13px;color:#1f2933;">{text}</td></tr>'
        for icon, text in rows
    )


def _badge_row(branding: TenantBranding, asset_url: AssetUrl) -> str:
    badges = "".join(
        f'<img src="{_e(asset_url(badge))}" alt="" height="28" '
        'style="height:28px;width:auto;border:0;margin:0 6px;vertical-align:middle;">'
        for badge in branding.badge_asset_ids
    )
    if not badges:
        return ""
    return (
        '<tr><td colspan="3" align="left" '
        f'style="padding:8px 10px;border-top:1px solid #e5e7eb;">{badges}</td></tr>'
    )


def _promo_row(branding: TenantBranding, company: str, asset_url: AssetUrl) -> str:
    if not branding.promo_asset_id:
        return ""
    image = (
        f'<img src="{_e(asset_url(branding.promo_asset_id))}" alt="{_e(company)}" width="600" '
        'style="display:block;width:100%;max-width:600px;height:auto;border:0;">'
    )
    if branding.promo_link:
        image = f'<a href="{_e(_absolute(branding.promo_link))}">{image}</a>'
    return f'<tr><td colspan="3" style="padding:0;">{image}</td></tr>'


def build_signature_text(sender: Sender, branding: TenantBranding, company_name: str) -> str:
    """Plain-text signature for the text alternative of the email."""
    lines = [
        sender.display_name,
        sender.job_title,
        branding.company_name or company_name,
        sender.phone or branding.phone,
        f"WhatsApp: {sender.mobile}" if sender.mobile else None,
        f"Fax: {branding.fax}" if branding.fax else None,
        sender.email,
        branding.address,
        branding.website,
    ]
    return "\n".join(line for line in lines if line)


def _digits(number: str) -> str:
    return "".join(character for character in number if character.isdigit())


def _dial(number: str) -> str:
    """`905-677-5000 Ex 332` → `+9056775000,332`-style dial string (extension after a pause)."""
    main, _, extension = number.lower().partition("ex")
    digits = _digits(main)
    prefix = "+" if main.strip().startswith("+") else ""
    ext = _digits(extension)
    return f"{prefix}{digits},{ext}" if ext else f"{prefix}{digits}"


def _absolute(url: str | None) -> str:
    if not url:
        return ""
    return url if url.startswith(("http://", "https://")) else f"https://{url}"
