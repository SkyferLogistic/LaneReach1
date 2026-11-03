"""The quote as the customer sees it: branded HTML (email body and printable page) and text."""

from __future__ import annotations

import html
from collections.abc import Sequence
from decimal import Decimal

from lanereach.domain.logistics import LINE_LABELS, MODES
from lanereach.domain.quotes.models import Quote, QuoteLine
from lanereach.domain.senders.models import ServiceLine
from lanereach.tenants.branding import TenantBranding

_CHARGE_LABELS = {
    "freight": "Freight",
    "origin": "Origin charges",
    "destination": "Destination charges",
    "customs": "Customs",
    "insurance": "Insurance",
    "storage": "Storage & handling",
    "surcharge": "Surcharges",
    "other": "Other",
}


# Inline styles (mail clients drop <style> blocks).
_WRAP = (
    "font-family:Calibri,Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;"
    "color:#1f2933;max-width:680px;"
)
_P = "margin:0 0 10px 0;"
_NOTES = "margin:14px 0 0 0;font-size:13px;color:#435069;"
_CARD = "border:1px solid #dde2ec;border-radius:10px;overflow:hidden;margin:14px 0;"
_BAND = "color:#ffffff;padding:12px 16px;"
_SMALL = "font-size:12px;opacity:.85;"
_TITLE = "font-size:18px;font-weight:700;"
_TABLE = "border-collapse:collapse;font-size:13px;"
_HEAD = "background:#f2f4f9;color:#435069;text-align:left;"
_CELL = "padding:8px 6px;"
_RIGHT = "text-align:right;white-space:nowrap;"
_MUTED = "font-size:12px;color:#6b7686;"
_FACT_LABEL = "padding:4px 12px 4px 0;color:#6b7686;"
_FACT_VALUE = "padding:4px 0;font-weight:600;"


def money(value: Decimal, currency: str) -> str:
    """`1,234.50 USD` (no locale guessing in an email)."""
    return f"{value:,.2f} {currency}"


def _quantity(value: Decimal) -> str:
    text = f"{value:,.3f}".rstrip("0").rstrip(".")
    return text or "0"


def mode_label(quote: Quote) -> str:
    """For example `Ocean freight · FCL (full container)`."""
    try:
        line = ServiceLine(quote.service_line)
    except ValueError:
        line = ServiceLine.ANY
    mode = next((option.label for option in MODES.get(line, ()) if option.key == quote.mode), None)
    return " · ".join(part for part in (LINE_LABELS.get(line), mode) if part)


def render_quote_html(
    quote: Quote,
    lines: Sequence[QuoteLine],
    *,
    branding: TenantBranding,
    company_name: str,
    contact_name: str,
    customer_company: str | None,
    intro: str | None = None,
    signature: str = "",
    online_url: str | None = None,
) -> str:
    """The quote as an HTML email body (inline styles only; mail clients drop `<style>`).

    Args:
        quote: The quote.
        lines: Its charge lines, in order.
        branding: Tenant colours and contact details.
        company_name: The forwarder's name.
        contact_name: Who it is for.
        customer_company: Their company.
        intro: Optional text above the table (escaped).
        signature: Trusted signature HTML appended at the end.
        online_url: The page where the customer accepts the quote online (a button).

    Returns:
        The HTML.
    """
    primary = branding.primary_color
    esc = html.escape
    facts = [
        ("Service", mode_label(quote)),
        (
            "Route",
            f"{quote.origin or '?'} → {quote.destination or '?'}"
            if quote.origin or quote.destination
            else None,
        ),
        ("Incoterm", quote.incoterm),
        ("Transit time", f"{quote.transit_days} days" if quote.transit_days else None),
        ("Valid until", quote.valid_until.strftime("%d %b %Y")),
    ]
    fact_rows = "".join(
        f'<tr><td style="{_FACT_LABEL}">{esc(label)}</td>'
        f'<td style="{_FACT_VALUE}">{esc(value)}</td></tr>'
        for label, value in facts
        if value
    )
    charge_rows = "".join(_charge_row(line, quote.currency) for line in lines)
    greeting = f"Dear {esc(contact_name)}," if contact_name else "Hello,"
    default_intro = f"Thank you for your request. Please find our offer {quote.number} below."
    paragraphs = [part for part in (intro or default_intro).split("\n\n") if part.strip()]
    intro_html = "".join(f'<p style="{_P}">{_text(part)}</p>' for part in paragraphs)
    notes = f'<p style="{_NOTES}">{_text(quote.notes)}</p>' if quote.notes.strip() else ""
    customer = (
        f'<div style="{_SMALL}">For {esc(customer_company)}</div>' if customer_company else ""
    )
    total = money(quote.total_amount, quote.currency)
    return (
        f'<div style="{_WRAP}">'
        f'<p style="{_P}">{greeting}</p>{intro_html}'
        f'<div style="{_CARD}">'
        f'<div style="background:{primary};{_BAND}">'
        f'<div style="{_SMALL}">{esc(company_name)} · Quotation</div>'
        f'<div style="{_TITLE}">{esc(quote.number)} · {esc(quote.title)}</div>'
        f"{customer}</div>"
        f'<div style="padding:12px 16px;"><table style="{_TABLE}">{fact_rows}</table></div>'
        f'<table style="{_TABLE}width:100%;">'
        f'<tr style="{_HEAD}"><th style="{_CELL}">Charge</th>'
        f'<th style="{_CELL}{_RIGHT}">Quantity</th>'
        f'<th style="{_CELL}{_RIGHT}">Unit price</th>'
        f'<th style="{_CELL}{_RIGHT}">Amount</th></tr>'
        f"{charge_rows}"
        '<tr style="border-top:2px solid #1f2933;">'
        f'<td colspan="3" style="{_CELL}{_RIGHT}font-weight:700;">Total</td>'
        f'<td style="{_CELL}{_RIGHT}font-weight:700;">{total}</td></tr>'
        "</table></div>"
        f"{notes}"
        f"{_online_button(online_url, primary)}"
        f'<p style="{_P}">{esc(_answer_line(online_url))}</p>'
        f"{signature}</div>"
    )


def _text(value: str) -> str:
    return html.escape(value).replace("\n", "<br>")


def _charge_row(line: QuoteLine, currency: str) -> str:
    kind = _CHARGE_LABELS.get(line.charge_type.value, line.charge_type.value)
    return (
        '<tr style="border-top:1px solid #e6eaf2;">'
        f'<td style="{_CELL}">{html.escape(line.description)}'
        f'<div style="{_MUTED}">{html.escape(kind)}</div></td>'
        f'<td style="{_CELL}{_RIGHT}">{_quantity(line.quantity)} {html.escape(line.unit)}</td>'
        f'<td style="{_CELL}{_RIGHT}">{money(line.unit_price, currency)}</td>'
        f'<td style="{_CELL}{_RIGHT}font-weight:600;">{money(line.amount, currency)}</td>'
        "</tr>"
    )


def _answer_line(online_url: str | None) -> str:
    if online_url:
        return (
            "Accept or decline online with the button above, or reply to this email to ask "
            "for changes."
        )
    return "Reply to this email to accept or ask for changes."


def _online_button(online_url: str | None, color: str) -> str:
    if not online_url:
        return ""
    return (
        f'<p style="margin:18px 0;"><a href="{html.escape(online_url, quote=True)}" '
        f'style="display:inline-block;background:{color};color:#ffffff;text-decoration:none;'
        'font-weight:700;padding:12px 22px;border-radius:8px;">View and accept online</a></p>'
    )


def render_quote_text(
    quote: Quote,
    lines: Sequence[QuoteLine],
    *,
    contact_name: str,
    intro: str | None = None,
    online_url: str | None = None,
) -> str:
    """The plain-text alternative of the quote email."""
    rows = [
        f"- {line.description}: {_quantity(line.quantity)} {line.unit} x "
        f"{money(line.unit_price, quote.currency)} = {money(line.amount, quote.currency)}"
        for line in lines
    ]
    parts = [
        f"Dear {contact_name}," if contact_name else "Hello,",
        intro or f"Thank you for your request. Please find our offer {quote.number} below.",
        f"{quote.number} · {quote.title}",
        mode_label(quote),
        f"Route: {quote.origin or '?'} -> {quote.destination or '?'}"
        if quote.origin or quote.destination
        else "",
        f"Incoterm: {quote.incoterm}" if quote.incoterm else "",
        f"Transit time: {quote.transit_days} days" if quote.transit_days else "",
        f"Valid until: {quote.valid_until:%d %b %Y}",
        "\n".join(rows),
        f"Total: {money(quote.total_amount, quote.currency)}",
        quote.notes.strip(),
        f"View and accept online: {online_url}" if online_url else "",
        _answer_line(online_url),
    ]
    return "\n\n".join(part for part in parts if part)


def render_quote_page(document_html: str, number: str) -> str:
    """A printable page around the quote (Save as PDF from the browser)."""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Quotation {html.escape(number)}</title>"
        "<style>body{margin:32px auto;max-width:720px;padding:0 16px;background:#fff}"
        "@media print{body{margin:0}}</style></head>"
        f"<body>{document_html}</body></html>"
    )
