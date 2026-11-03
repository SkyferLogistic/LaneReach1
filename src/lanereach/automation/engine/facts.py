"""Builds the flat facts that conditions are evaluated against."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lanereach.automation.conditions import VARIABLE_PREFIX
from lanereach.domain.companies.models import Company
from lanereach.domain.contacts.models import Contact

REPLY_VARIABLE = "reply"
LEAD_VARIABLE = "lead"


def _as_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def build_condition_facts(
    contact: Contact,
    company: Company | None,
    variables: Mapping[str, Any],
    *,
    branch_count: int | None = None,
) -> dict[str, Any]:
    """Collect every condition field's current value.

    `reply.*` comes from the latest classification stored on the enrollment (by the
    `reply.classified` trigger or an `ai_classify_reply` node); `lead.*` from the latest
    shipment extraction; `enrollment.variables.*` from scalar enrollment variables.

    Args:
        contact: The enrolled contact.
        company: Its company, if any.
        variables: Enrollment variables.
        branch_count: Number of company branches, when known.

    Returns:
        Mapping of field name to value (missing values are simply absent or None).
    """
    facts: dict[str, Any] = {
        "contact.email": contact.email,
        "contact.email_domain": contact.email_domain,
        "contact.first_name": contact.first_name,
        "contact.last_name": contact.last_name,
        "contact.job_title": contact.job_title,
        "contact.country": contact.country,
        "contact.city": contact.city,
        "contact.language": contact.language,
        "contact.stage": contact.stage,
        "contact.tags": list(contact.tags or []),
        "contact.owner": contact.owner_email,
        "contact.network": contact.network,
        "contact.created_at": contact.created_at,
    }
    if company is not None:
        facts |= {
            "company.name": company.name,
            "company.domain": company.domain,
            "company.country": company.country,
            "company.industry": company.industry,
            "company.is_frozen": company.frozen_at is not None,
            "company.branch_count": branch_count,
        }
    reply = variables.get(REPLY_VARIABLE)
    if isinstance(reply, Mapping):
        for key in ("bucket", "confidence", "language", "needs_human"):
            facts[f"reply.{key}"] = reply.get(key)
    lead = variables.get(LEAD_VARIABLE)
    if isinstance(lead, Mapping):
        origin = _as_mapping(lead.get("origin"))
        destination = _as_mapping(lead.get("destination"))
        facts |= {
            "lead.mode": lead.get("mode"),
            "lead.origin_country": origin.get("country"),
            "lead.destination_country": destination.get("country"),
            "lead.container_type": lead.get("container_type"),
            "lead.container_quantity": lead.get("container_quantity"),
            "lead.weight_kg": lead.get("weight_kg"),
            "lead.volume_cbm": lead.get("volume_cbm"),
            "lead.commodity": lead.get("commodity"),
            "lead.incoterm": lead.get("incoterm"),
            "lead.frequency": lead.get("frequency"),
            "lead.is_hazardous": lead.get("is_hazardous"),
        }
    for name, value in variables.items():
        if name not in (REPLY_VARIABLE, LEAD_VARIABLE) and (
            value is None or isinstance(value, str | int | float | bool)
        ):
            facts[f"{VARIABLE_PREFIX}{name}"] = value
    return facts
