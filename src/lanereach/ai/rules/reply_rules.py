"""Deterministic pre-classification of replies, before any LLM call.

Automatic messages (bounces, out-of-office, auto-replies) and unsubscribe requests are common,
unambiguous and must be handled reliably, so they are recognised with rules: no cost, no
latency, no model that could be talked out of it. Order of checks:

1. delivery failures from mailer daemons: `blocked` (policy/spam rejections), `bounce_hard`
   (permanent 5.x.x failures), `bounce_soft` (temporary 4.x.x failures);
2. unsubscribe requests (EN, FR, ES, PT, ZH);
3. out-of-office replies, with the return date when one is stated;
4. other auto-replies.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date

from lanereach.ai.schemas import ReplyBucket


@dataclass(frozen=True)
class ReplyMessage:
    """The parts of an inbound email the rules look at."""

    subject: str
    body: str
    received_on: date
    from_email: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RuleClassification:
    """A rule match."""

    bucket: ReplyBucket
    rule: str
    return_date: date | None = None
    dsn_code: str | None = None


_DAEMON_SENDER = re.compile(r"^(mailer-daemon|postmaster|mail-daemon|bounces?)[@+]", re.IGNORECASE)
_DSN_CODE = re.compile(r"\b([245])\.(\d{1,3})\.(\d{1,3})\b")
_BOUNCE_SUBJECT = re.compile(
    r"(undeliverable|undelivered mail|delivery status notification|mail delivery (failed|failure|system)|"
    r"returned mail|delivery failure|failure notice|non remis|no se puede entregar|não entregue)",
    re.IGNORECASE,
)
_BLOCKED_PHRASES = (
    "blocked",
    "blacklist",
    "blocklist",
    "spam policy",
    "rejected for policy reasons",
    "message rejected",
    "access denied",
    "5.7.1",
    "5.7.606",
)
_HARD_PHRASES = (
    "user unknown",
    "no such user",
    "does not exist",
    "address rejected",
    "recipient not found",
    "mailbox unavailable",
    "invalid recipient",
    "address not found",
    "unknown recipient",
)
_SOFT_PHRASES = (
    "mailbox full",
    "over quota",
    "quota exceeded",
    "temporarily",
    "try again later",
    "deferred",
    "temporary failure",
)

_UNSUBSCRIBE_PHRASES = (
    # English
    "unsubscribe",
    "remove me from",
    "take me off",
    "stop emailing",
    "stop sending",
    "do not contact me",
    "don't contact me",
    "opt out",
    "opt-out",
    # French
    "désabonner",
    "desabonner",
    "désinscrire",
    "desinscrire",
    "plus recevoir",  # "ne plus recevoir", "je ne veux plus recevoir"
    "retirez-moi",
    # Spanish
    "darme de baja",
    "dar de baja",
    "no me envíen",
    "no me envien",
    "eliminarme de",
    "quiero darme de baja",
    # Portuguese
    "descadastrar",
    "cancelar inscrição",
    "cancelar a inscrição",
    "remover da lista",
    "não quero receber",
    # Chinese
    "退订",
    "取消订阅",
    "不要再发",
    "请勿再发",
)

_OOO_SUBJECT = re.compile(
    r"(out of (the )?office|automatic reply|auto(matic)?[- ]?reply|autoreply|away from (the )?office|"
    r"on (annual )?leave|on vacation|absence|absent|réponse automatique|respuesta automática|"
    r"fuera de la oficina|ausente|resposta automática|fora do escritório|自动回复|不在办公室)",
    re.IGNORECASE,
)
_OOO_BODY = re.compile(
    r"(i am (currently )?(out of the office|away|on leave|on vacation|travelling)|"
    r"i('m| will be) (out of the office|away|on leave)|je suis absent|je serai absent|"
    r"estoy fuera|estaré fuera|me encuentro fuera|estou ausente|estarei ausente|我(目前)?不在办公室)",
    re.IGNORECASE,
)
_AUTO_REPLY_BODY = re.compile(
    r"(this is an automat(ed|ic) (reply|response|message)|do not reply to this (email|message)|"
    r"thank you for your (email|message)[,.]? (we|it) (will|has been received)|"
    r"votre message a bien été reçu|hemos recibido su mensaje|recebemos a sua mensagem)",
    re.IGNORECASE,
)

_MONTHS = {
    # English
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
    # French
    "janvier": 1,
    "février": 2,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "août": 8,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "décembre": 12,
    "decembre": 12,
    # Spanish
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
    # Portuguese
    "janeiro": 1,
    "fevereiro": 2,
    "março": 3,
    "marco": 3,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "setembro": 9,
    "outubro": 10,
    "dezembro": 12,
}
_MONTH_NAMES = "|".join(sorted((re.escape(name) for name in _MONTHS), key=len, reverse=True))
_RETURN_CUE = (
    r"(?:back|return(?:ing)?|returns?|until|till|from|de retour|retour|jusqu'au|jusqu’au|"
    r"regreso|regresaré|vuelvo|hasta|volto|retorno|até|返回|回来)"
)
_DAY_MONTH = re.compile(
    rf"{_RETURN_CUE}[^.\n]{{0,25}}?\b(?P<day>\d{{1,2}})(?:st|nd|rd|th|er)?\s*(?:de\s+)?"
    rf"(?P<month>{_MONTH_NAMES})\.?(?:\s*(?:de\s+)?(?P<year>\d{{4}}))?",
    re.IGNORECASE,
)
_MONTH_DAY = re.compile(
    rf"{_RETURN_CUE}[^.\n]{{0,25}}?\b(?P<month>{_MONTH_NAMES})\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?"
    rf"(?:,?\s*(?P<year>\d{{4}}))?",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(
    rf"{_RETURN_CUE}[^.\n]{{0,25}}?\b(?P<year>\d{{4}})-(?P<month>\d{{2}})-(?P<day>\d{{2}})\b",
    re.IGNORECASE,
)
_NUMERIC_DATE = re.compile(
    rf"{_RETURN_CUE}[^.\n]{{0,25}}?\b(?P<day>\d{{1,2}})[/.](?P<month>\d{{1,2}})(?:[/.](?P<year>\d{{2,4}}))?\b",
    re.IGNORECASE,
)
_CHINESE_DATE = re.compile(r"(?:(?P<year>\d{4})年)?(?P<month>\d{1,2})月(?P<day>\d{1,2})[日号]")


def _build_date(year: str | None, month: int, day: int, reference: date) -> date | None:
    try:
        if year:
            full_year = int(year) + 2000 if len(year) == 2 else int(year)
            return date(full_year, month, day)
        candidate = date(reference.year, month, day)
    except ValueError:
        return None
    # A date without a year that already passed means next year (for example in late December).
    return candidate if candidate >= reference else candidate.replace(year=reference.year + 1)


def extract_return_date(text: str, reference: date) -> date | None:
    """Find the return date in an out-of-office message.

    Understands "back on 12 October", "until October 12, 2026", "de retour le 5 novembre",
    "regreso el 3 de diciembre", "volto em 2 de outubro", ISO and day/month numeric dates, and
    Chinese "10月12日". Numeric dates are read day first (European and Gulf convention).

    Args:
        text: Subject and body.
        reference: Date the message was received (for dates without a year).

    Returns:
        The return date, or None.
    """
    for pattern in (_ISO_DATE, _DAY_MONTH, _MONTH_DAY, _NUMERIC_DATE, _CHINESE_DATE):
        match = pattern.search(text)
        if not match:
            continue
        raw_month = match.group("month")
        month = (
            _MONTHS.get(raw_month.lower().rstrip("."))
            if not raw_month.isdigit()
            else int(raw_month)
        )
        if month is None or not 1 <= month <= 12:
            continue
        found = _build_date(match.group("year"), month, int(match.group("day")), reference)
        if found is not None:
            return found
    return None


def _contains_any(text: str, phrases: tuple[str, ...]) -> str | None:
    return next((phrase for phrase in phrases if phrase in text), None)


def classify_reply_by_rules(message: ReplyMessage) -> RuleClassification | None:
    """Classify automatic messages and unsubscribe requests without AI.

    Args:
        message: The inbound email.

    Returns:
        The classification, or None when the reply needs the AI classifier.
    """
    subject = message.subject or ""
    body = message.body or ""
    text = f"{subject}\n{body}"
    lowered = text.lower()
    headers = {key.lower(): value.lower() for key, value in message.headers.items()}

    is_daemon = bool(
        _DAEMON_SENDER.match(message.from_email or "")
    ) or "multipart/report" in headers.get("content-type", "")
    if is_daemon or _BOUNCE_SUBJECT.search(subject):
        codes = _DSN_CODE.findall(text)
        dsn = ".".join(codes[0]) if codes else None
        if _contains_any(lowered, _BLOCKED_PHRASES):
            return RuleClassification(
                ReplyBucket.BLOCKED, "delivery rejected by policy", dsn_code=dsn
            )
        if (dsn and dsn.startswith("4")) or _contains_any(lowered, _SOFT_PHRASES):
            return RuleClassification(
                ReplyBucket.BOUNCE_SOFT, "temporary delivery failure", dsn_code=dsn
            )
        if (dsn and dsn.startswith("5")) or _contains_any(lowered, _HARD_PHRASES) or is_daemon:
            return RuleClassification(
                ReplyBucket.BOUNCE_HARD, "permanent delivery failure", dsn_code=dsn
            )

    phrase = _contains_any(lowered, _UNSUBSCRIBE_PHRASES)
    if phrase:
        return RuleClassification(ReplyBucket.UNSUBSCRIBE, f"unsubscribe request ('{phrase}')")

    auto_header = headers.get("auto-submitted", "no") not in ("", "no") or "x-autoreply" in headers
    if _OOO_SUBJECT.search(subject) or _OOO_BODY.search(body):
        return RuleClassification(
            ReplyBucket.OUT_OF_OFFICE,
            "out-of-office reply",
            return_date=extract_return_date(text, message.received_on),
        )
    if (
        auto_header
        or _AUTO_REPLY_BODY.search(body)
        or headers.get("precedence") in ("auto_reply", "bulk")
    ):
        return RuleClassification(ReplyBucket.AUTO_REPLY, "automatic reply")
    return None
