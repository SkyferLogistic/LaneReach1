"""Base class for workflow nodes.

A node is a building block users place on the workflow canvas. Each node class declares its
metadata (type, category, description), a strict Pydantic config model (its JSON Schema is what
the canvas renders as a form), its output handles and structural flags the validator relies on.
"""

from __future__ import annotations

from abc import ABC
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from lanereach.automation.engine.context import ExecutionContext
from lanereach.automation.engine.results import NodeResult


class NodeCategory(StrEnum):
    """Palette group of a node."""

    TRIGGER = "trigger"
    ACTION = "action"
    LOGIC = "logic"
    AI = "ai"
    PROTECTION = "protection"


class NodeConfig(BaseModel):
    """Base for node configuration models. Unknown settings are rejected."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EmptyNodeConfig(NodeConfig):
    """This node has no settings."""


class BaseNode[ConfigT: NodeConfig](ABC):
    """A building block users place on the workflow canvas.

    Class attributes:
        node_type: Stable identifier stored in workflow graphs, for example `send_email`.
        category: Palette group.
        display_name: Label shown on the canvas.
        description: One or two sentences shown in the palette.
        config_model: Pydantic model validating the node's `config`.
        output_handles: Handles edges may leave from. Nodes whose handles depend on their config
            (`branch_on_reply_bucket`, `ab_split`) override `handles_for`.
        is_terminal: A path may end at this node (see the "every path ends" validation rule).
        is_cold_send: Sends a cold email; counted against `MAX_COLD_EMAILS_PER_SEQUENCE` and
            must be separated from the previous send by a wait.
        is_wait: Delays the enrollment.
    """

    node_type: ClassVar[str]
    category: ClassVar[NodeCategory]
    display_name: ClassVar[str]
    description: ClassVar[str]
    config_model: ClassVar[type[NodeConfig]] = EmptyNodeConfig
    output_handles: ClassVar[tuple[str, ...]] = ("next",)
    has_dynamic_handles: ClassVar[bool] = False
    is_terminal: ClassVar[bool] = False
    is_cold_send: ClassVar[bool] = False
    is_wait: ClassVar[bool] = False

    @classmethod
    def parse_config(cls, raw: Mapping[str, Any]) -> NodeConfig:
        """Validate a raw config dict against `config_model`.

        Args:
            raw: The `config` object from the workflow graph.

        Returns:
            The validated config.

        Raises:
            pydantic.ValidationError: If the config is invalid.
        """
        return cls.config_model.model_validate(raw)

    @classmethod
    def handles_for(cls, config: NodeConfig) -> tuple[str, ...]:
        """Return the output handles for a given config.

        Args:
            config: A validated config of this node.

        Returns:
            Handle names edges may use as `handle`.
        """
        return cls.output_handles

    async def execute(self, context: ExecutionContext, config: ConfigT) -> NodeResult:
        """Run the node for one enrollment step.

        Args:
            context: The enrollment step being executed.
            config: The node's validated config.

        Returns:
            What the executor should do next.

        Raises:
            NotImplementedError: For nodes whose runtime support has not been built yet.
        """
        raise NotImplementedError(f"{self.node_type} runs once the engine lands (M4)")
