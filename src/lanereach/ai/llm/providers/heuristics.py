"""Deterministic stand-ins for the AI tasks, used by the mock provider.

They make the product usable end to end without an API key (dev, demos, the frontend) and give
tests realistic behaviour. They are intentionally simple: keyword classification, regex
extraction with a small port gazetteer, template-based drafts. They are never used when a real
provider is configured.
"""

from __future__ import annotations

import json
import re
from typing import Any

EMAIL_BLOCK = re.compile(r"<email_content>\s*(.*?)\s*</email_content>", re.DOTALL)
CONTEXT_BLOCK = re.compile(r"<(draft_context|template_content)>\s*(.*?)\s*</\1>", re.DOTALL)

# (keywords, bucket, confidence). First match wins, so specific intents come first.
_BUCKET_RULES: list[tuple[tuple[str, ...], str, float]] = [
    (
        (
            "spam",
            "report you",
            "harass",
            "stop spamming",
            "how did you get my",
            "denuncia",
            "plainte",
        ),
        "complaint",
        0.9,
    ),
    (
        (
            "wrong person",
            "not the right person",
            "wrong office",
            "not responsible",
            "no soy la persona",
            "pas la bonne personne",
        ),
        "wrong_office",
        0.85,
    ),
    (
        (
            "please contact",
            "reach out to my colleague",
            "cc'd my colleague",
            "forwarding to",
            "hablar con mi colega",
            "contactez mon collègue",
        ),
        "referral",
        0.8,
    ),
    (
        (
            "already work with you",
            "existing customer",
            "already your customer",
            "ya trabajamos con ustedes",
            "déjà client",
        ),
        "existing_partner",
        0.85,
    ),
    (
        (
            "quote",
            "quotation",
            "rate for",
            "rates for",
            "price for",
            "how much",
            "cotización",
            "cotizacion",
            "tarifa",
            "devis",
            "tarif",
            "cotação",
            "cotacao",
            "orçamento",
            "报价",
            "运费",
        ),
        "rate_query",
        0.9,
    ),
    (
        (
            "meeting",
            "call next week",
            "schedule a call",
            "let's talk",
            "teams call",
            "reunión",
            "réunion",
            "reunião",
            "开会",
        ),
        "meeting_request",
        0.85,
    ),
    (
        (
            "partnership",
            "agency agreement",
            "become your agent",
            "be your partner",
            "asociación",
            "partenariat",
            "parceria",
            "合作",
        ),
        "partnership_query",
        0.8,
    ),
    (
        (
            "do you handle",
            "do you offer",
            "do you cover",
            "services do you",
            "customs clearance",
            "warehousing",
            "ofrecen",
            "proposez-vous",
            "vocês oferecem",
        ),
        "service_query",
        0.8,
    ),
    (
        (
            "not interested",
            "no thanks",
            "no thank you",
            "not at this time",
            "no estamos interesados",
            "pas intéressé",
            "não temos interesse",
            "不感兴趣",
        ),
        "not_interested",
        0.9,
    ),
    (
        (
            "next quarter",
            "later this year",
            "in a few months",
            "contact me in",
            "más adelante",
            "plus tard",
            "mais tarde",
            "以后",
        ),
        "later",
        0.8,
    ),
    (
        (
            "interested",
            "sounds good",
            "tell me more",
            "send more information",
            "me interesa",
            "intéressé",
            "interessado",
            "感兴趣",
        ),
        "interested",
        0.85,
    ),
]

_LANGUAGE_MARKERS = {
    "fr": ("bonjour", "merci", "nous", "votre", "cordialement", "devis"),
    "es": ("hola", "gracias", "nuestro", "saludos", "cotización", "cotizacion", "estamos"),
    "pt": ("olá", "obrigado", "obrigada", "nosso", "cotação", "atenciosamente", "vocês"),
    "de": ("hallo", "danke", "unser", "grüße", "angebot"),
}

# City -> (country, UN/LOCODE). Enough for demos and the eval set.
PORTS: dict[str, tuple[str, str]] = {
    "chennai": ("IN", "INMAA"),
    "mumbai": ("IN", "INBOM"),
    "nhava sheva": ("IN", "INNSA"),
    "rotterdam": ("NL", "NLRTM"),
    "antwerp": ("BE", "BEANR"),
    "hamburg": ("DE", "DEHAM"),
    "jebel ali": ("AE", "AEJEA"),
    "dubai": ("AE", "AEDXB"),
    "abu dhabi": ("AE", "AEAUH"),
    "jeddah": ("SA", "SAJED"),
    "dammam": ("SA", "SADMM"),
    "riyadh": ("SA", "SARUH"),
    "alexandria": ("EG", "EGALY"),
    "port said": ("EG", "EGPSD"),
    "shanghai": ("CN", "CNSHA"),
    "ningbo": ("CN", "CNNGB"),
    "shenzhen": ("CN", "CNSZX"),
    "valencia": ("ES", "ESVLC"),
    "barcelona": ("ES", "ESBCN"),
    "algeciras": ("ES", "ESALG"),
    "le havre": ("FR", "FRLEH"),
    "marseille": ("FR", "FRMRS"),
    "santos": ("BR", "BRSSZ"),
    "colombo": ("LK", "LKCMB"),
    "new york": ("US", "USNYC"),
    "los angeles": ("US", "USLAX"),
    "houston": ("US", "USHOU"),
    "montreal": ("CA", "CAMTR"),
    "toronto": ("CA", "CATOR"),
    "vancouver": ("CA", "CAVAN"),
    "felixstowe": ("GB", "GBFXT"),
}

_CONTAINER = re.compile(
    r"(?P<qty>\d{1,4})\s*(?:x|×|\*)?\s*(?P<size>20|40|45)\s*['’]?\s*(?:ft|foot)?\s*(?P<kind>hc|hq|gp|dv|rf|reefer|high cube|dry)?",
    re.IGNORECASE,
)
_INCOTERM = re.compile(r"\b(EXW|FCA|CPT|CIP|DAP|DPU|DDP|FAS|FOB|CFR|CIF)\b")
_WEIGHT = re.compile(
    r"(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>kg|kgs|kilos|t|tons|tonnes|mt)\b", re.IGNORECASE
)
_VOLUME = re.compile(r"(?P<value>\d+(?:[.,]\d+)?)\s*(?:cbm|m3|m³)\b", re.IGNORECASE)
_ROUTE = re.compile(
    r"\b(?:from|ex|de|desde|da)\s+(?P<origin>[A-Za-zÀ-ÿ .]{3,30}?)\s+(?:to|a|à|para|hasta|->)\s+(?P<destination>[A-Za-zÀ-ÿ .]{3,30}?)(?=[,.;\n]|\s+(?:monthly|weekly|fob|cif|exw|every|per|each|mensual|mensuel)|$)",
    re.IGNORECASE,
)
_FREQUENCY = {
    "weekly": ("weekly", "every week", "semanal", "hebdomadaire", "每周"),
    "monthly": ("monthly", "every month", "per month", "mensual", "mensuel", "mensal", "每月"),
    "quarterly": ("quarterly", "every quarter", "trimestral", "trimestriel"),
    "one_off": ("one-off", "one off", "single shipment", "one time", "une fois"),
}
_COMMODITY = re.compile(
    r"\b(?:of|with|containing|cargo:?)\s+(?P<commodity>[a-z][a-z \-]{2,40}?)(?=[,.;\n]|\s+(?:from|to|monthly|weekly)|$)",
    re.IGNORECASE,
)


def extract_email_text(messages_text: str) -> str:
    """Return the text inside `<email_content>` delimiters (or everything if absent)."""
    match = EMAIL_BLOCK.search(messages_text)
    return match.group(1) if match else messages_text


def extract_context(messages_text: str) -> dict[str, Any]:
    """Parse the JSON inside a `<draft_context>` or `<template_content>` block."""
    match = CONTEXT_BLOCK.search(messages_text)
    if not match:
        return {}
    try:
        data = json.loads(match.group(2))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def detect_language(text: str) -> str:
    """Rough language guess from marker words (defaults to English)."""
    if re.search(r"[一-鿿]", text):
        return "zh"
    lowered = text.lower()
    scores = {
        code: sum(marker in lowered for marker in markers)
        for code, markers in _LANGUAGE_MARKERS.items()
    }
    best = max(scores, key=lambda code: scores[code])
    return best if scores[best] > 0 else "en"


_INJECTION = re.compile(
    r"(ignore (all |any )?(previous|prior|above) instructions|disregard (the |your )?(system|previous)|"
    r"you are now|new instructions:|system prompt|classify (this|me) as|set (the )?bucket|"
    r"reveal|send me (the |a )?list of|system:)",
    re.IGNORECASE,
)


def classify_heuristically(text: str) -> dict[str, Any]:
    """Keyword classification of a reply.

    Like the real prompt, an email that tries to instruct the classifier is `unclear` and goes
    to a human instead of getting the bucket it asks for.
    """
    lowered = text.lower()
    if _INJECTION.search(text):
        return {
            "bucket": "unclear",
            "confidence": 0.3,
            "language": detect_language(text),
            "summary": "The message contains instructions aimed at the classifier.",
            "reasoning_short": "Possible prompt injection; needs a person.",
            "needs_human": True,
        }
    for keywords, bucket, confidence in _BUCKET_RULES:
        hit = next((keyword for keyword in keywords if keyword in lowered), None)
        if hit:
            return {
                "bucket": bucket,
                "confidence": confidence,
                "language": detect_language(text),
                "summary": _summarise(text),
                "reasoning_short": f"Mentions '{hit}'.",
                "needs_human": bucket in {"complaint", "partnership_query"},
            }
    return {
        "bucket": "unclear",
        "confidence": 0.5,
        "language": detect_language(text),
        "summary": _summarise(text),
        "reasoning_short": "No clear intent found.",
        "needs_human": True,
    }


def _summarise(text: str) -> str:
    sentence = re.split(r"(?<=[.!?。])\s+", " ".join(text.split()))[0]
    return sentence[:200]


def _place(name: str) -> dict[str, Any]:
    cleaned = name.strip(" .").lower()
    for city, (country, code) in PORTS.items():
        if city in cleaned:
            return {"city": city.title(), "country": country, "port_code": code}
    return {"city": name.strip(" .").title()[:100], "country": None, "port_code": None}


def extract_shipment_heuristically(text: str) -> dict[str, Any]:
    """Regex extraction of shipment details."""
    result: dict[str, Any] = {
        "origin": None,
        "destination": None,
        "mode": "UNKNOWN",
        "container_type": None,
        "container_quantity": None,
        "commodity": None,
        "weight_kg": None,
        "volume_cbm": None,
        "frequency": "unknown",
        "incoterm": None,
        "cargo_ready_date": None,
        "is_hazardous": None,
        "special_requirements": None,
        "missing_fields": [],
        "confidence": 0.6,
    }
    route = _ROUTE.search(text)
    if route:
        result["origin"] = _place(route.group("origin"))
        result["destination"] = _place(route.group("destination"))
    else:
        found = [city for city in PORTS if city in text.lower()]
        if len(found) >= 2:
            ordered = sorted(found, key=lambda city: text.lower().index(city))
            result["origin"], result["destination"] = _place(ordered[0]), _place(ordered[1])
    container = _CONTAINER.search(text)
    if container:
        size, kind = container.group("size"), (container.group("kind") or "gp").lower()
        code = {"hc": "HC", "hq": "HC", "high cube": "HC", "rf": "RF", "reefer": "RF"}.get(
            kind, "GP"
        )
        container_type = f"{size}{code}"
        allowed = {"20GP", "40GP", "40HC", "45HC", "20RF", "40RF"}
        result["container_type"] = container_type if container_type in allowed else "OTHER"
        result["container_quantity"] = int(container.group("qty"))
        result["mode"] = "FCL"
    lowered = text.lower()
    if any(
        word in lowered
        for word in ("air freight", "by air", "airfreight", "aéreo", "aérien", "空运")
    ):
        result["mode"] = "AIR"
    elif "lcl" in lowered or "groupage" in lowered:
        result["mode"] = "LCL"
    elif "fcl" in lowered and result["mode"] == "UNKNOWN":
        result["mode"] = "FCL"
    elif any(word in lowered for word in ("truck", "road freight", "ftl", "ltl")):
        result["mode"] = "ROAD"
    incoterm = _INCOTERM.search(text.upper())
    if incoterm:
        result["incoterm"] = incoterm.group(1)
    weight = _WEIGHT.search(text)
    if weight:
        value = float(weight.group("value").replace(",", "."))
        result["weight_kg"] = (
            value * 1000 if weight.group("unit").lower() in {"t", "tons", "tonnes", "mt"} else value
        )
    volume = _VOLUME.search(text)
    if volume:
        result["volume_cbm"] = float(volume.group("value").replace(",", "."))
    for frequency, words in _FREQUENCY.items():
        if any(word in lowered for word in words):
            result["frequency"] = frequency
            break
    commodity = _COMMODITY.search(text)
    if commodity and commodity.group("commodity").strip().lower() not in PORTS:
        result["commodity"] = commodity.group("commodity").strip()
    if any(
        word in lowered for word in ("hazardous", "dangerous goods", "imo", "dg cargo", "un number")
    ):
        result["is_hazardous"] = True
    filled = sum(
        result[key] is not None
        for key in ("origin", "destination", "container_type", "incoterm", "weight_kg")
    )
    result["confidence"] = round(min(0.95, 0.4 + 0.12 * filled), 2)
    return result


_GREETING = {"en": "Hello", "fr": "Bonjour", "es": "Hola", "pt": "Olá", "de": "Hallo", "zh": "您好"}
_BODY = {
    "rate_query": {
        "en": "Thank you for your request. Our pricing team is preparing a quotation for this lane and will come back to you shortly.",
        "fr": "Merci pour votre demande. Notre équipe tarifaire prépare une cotation pour cette route et reviendra vers vous rapidement.",
        "es": "Gracias por su solicitud. Nuestro equipo de precios está preparando una cotización para esta ruta y le responderá en breve.",
        "pt": "Obrigado pelo seu pedido. A nossa equipa de preços está a preparar uma cotação para esta rota e responderá em breve.",
    },
    "interested": {
        "en": "Thank you for your interest. I would be glad to arrange a short call to understand your shipping needs.",
        "fr": "Merci de votre intérêt. Je serais ravi d'organiser un court appel pour comprendre vos besoins.",
        "es": "Gracias por su interés. Me encantaría organizar una breve llamada para entender sus necesidades.",
        "pt": "Obrigado pelo seu interesse. Terei todo o gosto em marcar uma breve chamada.",
    },
}


def draft_heuristically(context: dict[str, Any]) -> dict[str, Any]:
    """Template-like draft in the recipient's language."""
    language = str(context.get("language") or "en")
    bucket = str(context.get("bucket") or "interested")
    bodies = _BODY.get(bucket, _BODY["interested"])
    first_name = context.get("contact_first_name") or ""
    greeting = f"{_GREETING.get(language, 'Hello')} {first_name}".strip() + ","
    text = bodies.get(language, bodies["en"])
    signature = "\n".join(
        str(part) for part in (context.get("sender_name"), context.get("office_name")) if part
    )
    subject = str(context.get("original_subject") or "Your enquiry")
    return {
        "subject": subject if subject.lower().startswith("re:") else f"Re: {subject}",
        "body": f"{greeting}\n\n{text}\n\n{signature}".strip(),
        "language": language,
        "tone": str(context.get("tone") or "friendly"),
        "used_template_key": context.get("template_key"),
    }


def localize_heuristically(context: dict[str, Any]) -> dict[str, Any]:
    """Echo the template (placeholders untouched) tagged for the target language."""
    language = str(context.get("target_language") or "en")
    return {
        "subject": str(context.get("subject") or ""),
        "body": str(context.get("body") or ""),
        "language": language,
        "notes_for_reviewer": "Mock provider: text was not translated. Configure a real LLM provider.",
    }
