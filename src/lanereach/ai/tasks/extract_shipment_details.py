"""`extract_shipment_details`: turn a rate request into quote-ready fields."""

from __future__ import annotations

from lanereach.ai.prompts.registry import wrap_untrusted
from lanereach.ai.rules.reply_rules import ReplyMessage
from lanereach.ai.schemas import ShipmentDetails, ShippingMode
from lanereach.ai.tasks.runtime import AIRuntime, TaskResult, run_prompted_task

TASK = "extract_shipment_details"


def quote_gaps(details: ShipmentDetails) -> list[str]:
    """Fields pricing still needs, computed from the data (the model's own list is not trusted).

    Args:
        details: Extracted details.

    Returns:
        Missing field names.
    """
    missing: list[str] = []
    if details.origin is None or not (details.origin.city or details.origin.port_code):
        missing.append("origin")
    if details.destination is None or not (
        details.destination.city or details.destination.port_code
    ):
        missing.append("destination")
    if details.mode is ShippingMode.UNKNOWN:
        missing.append("mode")
    if details.mode is ShippingMode.FCL:
        if details.container_type is None:
            missing.append("container_type")
        if details.container_quantity is None:
            missing.append("container_quantity")
    elif details.mode in (
        ShippingMode.LCL,
        ShippingMode.AIR,
        ShippingMode.ROAD,
        ShippingMode.COURIER,
    ):
        if details.weight_kg is None and details.volume_cbm is None:
            missing.append("weight_kg")
    if not details.commodity:
        missing.append("commodity")
    return missing


async def extract_shipment_details(
    runtime: AIRuntime, message: ReplyMessage, *, input_ref: str | None = None
) -> TaskResult[ShipmentDetails]:
    """Extract shipment details from a reply.

    Args:
        runtime: AI runtime.
        message: The reply.
        input_ref: Message ID for the AI run.

    Returns:
        The details (with `missing_fields` recomputed) and the AI run ID.

    Raises:
        LLMError: If the model call fails.
    """
    result, run_id = await run_prompted_task(
        runtime,
        task=TASK,
        values={"email_content": wrap_untrusted(f"Subject: {message.subject}\n\n{message.body}")},
        output_model=ShipmentDetails,
        input_ref=input_ref,
        max_tokens=2048,
    )
    details = result.output
    details = details.model_copy(
        update={"missing_fields": sorted(set(quote_gaps(details)) | set(details.missing_fields))}
    )
    return TaskResult(details, run_id)
