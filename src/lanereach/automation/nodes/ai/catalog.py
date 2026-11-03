"""AI nodes: run AI tasks inside a workflow.

They store results on the enrollment; they never send email or decide whether email may be sent
(guardrails and policy do). The work itself lives in `lanereach.ai.workflow_steps`, imported
when a node runs because the AI package builds on the engine.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from lanereach.automation.engine.context import ExecutionContext
from lanereach.automation.engine.results import NodeResult
from lanereach.automation.nodes.base import BaseNode, NodeCategory, NodeConfig
from lanereach.automation.nodes.fields import RegionCode, TemplateKey, TemplateLanguage
from lanereach.automation.nodes.registry import register_node


class ClassifyReplyConfig(NodeConfig):
    """Optional stricter review threshold for this workflow."""

    review_below_confidence: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Send to human review below this confidence (defaults to the tenant setting).",
    )


@register_node
class AIClassifyReplyNode(BaseNode[ClassifyReplyConfig]):
    """Classifies the contact's latest reply."""

    node_type = "ai_classify_reply"
    category = NodeCategory.AI
    display_name = "AI: classify reply"
    description = "Classifies the latest reply into a bucket; results feed `reply.*` conditions."
    config_model = ClassifyReplyConfig

    async def execute(self, context: ExecutionContext, config: ClassifyReplyConfig) -> NodeResult:
        """Classify the reply (or reuse the pipeline's classification)."""
        from lanereach.ai.workflow_steps import classify_reply_step

        return await classify_reply_step(
            context, review_below_confidence=config.review_below_confidence
        )


class ExtractShipmentConfig(NodeConfig):
    """No settings."""


@register_node
class AIExtractShipmentDetailsNode(BaseNode[ExtractShipmentConfig]):
    """Extracts shipment details from the latest reply."""

    node_type = "ai_extract_shipment_details"
    category = NodeCategory.AI
    display_name = "AI: extract shipment"
    description = "Extracts origin, destination, mode, containers and more; feeds `lead.*`."
    config_model = ExtractShipmentConfig

    async def execute(self, context: ExecutionContext, config: ExtractShipmentConfig) -> NodeResult:
        """Extract shipment details (or reuse the pipeline's extraction)."""
        from lanereach.ai.workflow_steps import extract_shipment_step

        return await extract_shipment_step(context)


class DraftReplyConfig(NodeConfig):
    """How to draft."""

    template_key: TemplateKey | None = Field(
        default=None, description="Template to base the draft on."
    )
    tone: Literal["friendly", "formal", "concise"] = "friendly"


@register_node
class AIDraftReplyNode(BaseNode[DraftReplyConfig]):
    """Drafts a reply in the office's voice and the contact's language."""

    node_type = "ai_draft_reply"
    category = NodeCategory.AI
    display_name = "AI: draft reply"
    description = "Drafts a reply in the office voice; drafts go to review unless auto-send is on."
    config_model = DraftReplyConfig

    async def execute(self, context: ExecutionContext, config: DraftReplyConfig) -> NodeResult:
        """Draft a reply into the review queue (`variables.draft`)."""
        from lanereach.ai.workflow_steps import draft_reply_step

        return await draft_reply_step(context, template_key=config.template_key, tone=config.tone)


class LocalizeTemplateConfig(NodeConfig):
    """Target language and region."""

    template_key: TemplateKey
    target_language: TemplateLanguage
    region: RegionCode | None = None


@register_node
class AILocalizeTemplateNode(BaseNode[LocalizeTemplateConfig]):
    """Translates and adapts a template."""

    node_type = "ai_localize_template"
    category = NodeCategory.AI
    display_name = "AI: localize template"
    description = "Adapts a template to a language and region, keeping placeholders intact."
    config_model = LocalizeTemplateConfig

    async def execute(
        self, context: ExecutionContext, config: LocalizeTemplateConfig
    ) -> NodeResult:
        """Queue a localized template version for review."""
        from lanereach.ai.workflow_steps import localize_template_step

        return await localize_template_step(
            context,
            template_key=config.template_key,
            target_language=config.target_language,
            region=config.region,
        )
