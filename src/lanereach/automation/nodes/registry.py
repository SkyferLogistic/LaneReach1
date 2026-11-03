"""Node registry: the set of node types workflows may use.

Node classes register themselves with `@register_node`. Registering the same `node_type` twice
raises at import time, so a duplicate fails application startup instead of silently shadowing
another node.
"""

from __future__ import annotations

import importlib
import re
from typing import Any

from lanereach.automation.nodes.base import BaseNode, NodeCategory, NodeConfig

_NODE_TYPE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_HANDLE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_CATEGORY_ORDER = list(NodeCategory)

NODE_MODULES = (
    "lanereach.automation.nodes.triggers.catalog",
    "lanereach.automation.nodes.actions.catalog",
    "lanereach.automation.nodes.actions.crm",
    "lanereach.automation.nodes.actions.connect",
    "lanereach.automation.nodes.actions.sales",
    "lanereach.automation.nodes.logic.catalog",
    "lanereach.automation.nodes.ai.catalog",
    "lanereach.automation.nodes.protection.catalog",
)

type NodeClass = type[BaseNode[Any]]


class NodeRegistrationError(Exception):
    """A node class is malformed or its `node_type` is already registered."""


class UnknownNodeTypeError(LookupError):
    """No node is registered under the requested type."""


class NodeRegistry:
    """Maps `node_type` to node classes."""

    def __init__(self) -> None:
        self._nodes: dict[str, NodeClass] = {}

    def register(self, node_class: NodeClass) -> NodeClass:
        """Add a node class.

        Args:
            node_class: The class to register.

        Returns:
            The same class (so this can back a decorator).

        Raises:
            NodeRegistrationError: If metadata is missing or malformed, or the type is taken.
        """
        for attribute in ("node_type", "category", "display_name", "description"):
            if not getattr(node_class, attribute, None):
                raise NodeRegistrationError(f"{node_class.__name__} is missing '{attribute}'")
        node_type = node_class.node_type
        if not _NODE_TYPE.fullmatch(node_type):
            raise NodeRegistrationError(f"invalid node_type '{node_type}'")
        is_trigger = node_class.category is NodeCategory.TRIGGER
        if is_trigger != node_type.startswith("trigger_"):
            raise NodeRegistrationError(
                f"'{node_type}': trigger nodes (and only trigger nodes) start with 'trigger_'"
            )
        if not issubclass(node_class.config_model, NodeConfig):
            raise NodeRegistrationError(f"'{node_type}': config_model must extend NodeConfig")
        for handle in node_class.output_handles:
            if not _HANDLE.fullmatch(handle):
                raise NodeRegistrationError(f"'{node_type}': invalid handle '{handle}'")
        if node_type in self._nodes:
            raise NodeRegistrationError(f"node_type '{node_type}' is already registered")
        self._nodes[node_type] = node_class
        return node_class

    def find(self, node_type: str) -> NodeClass | None:
        """Return the class registered for `node_type`, or None.

        Args:
            node_type: Node type identifier.

        Returns:
            The node class or None.
        """
        return self._nodes.get(node_type)

    def get(self, node_type: str) -> NodeClass:
        """Return the class registered for `node_type`.

        Args:
            node_type: Node type identifier.

        Returns:
            The node class.

        Raises:
            UnknownNodeTypeError: If nothing is registered under that type.
        """
        node_class = self._nodes.get(node_type)
        if node_class is None:
            raise UnknownNodeTypeError(node_type)
        return node_class

    def all_nodes(self) -> list[NodeClass]:
        """Return every registered node, grouped by category then sorted by type.

        Returns:
            Node classes in palette order.
        """
        return sorted(
            self._nodes.values(),
            key=lambda node: (_CATEGORY_ORDER.index(node.category), node.node_type),
        )

    def __contains__(self, node_type: object) -> bool:
        return node_type in self._nodes

    def __len__(self) -> int:
        return len(self._nodes)


default_registry = NodeRegistry()


def register_node[NodeT: NodeClass](node_class: NodeT) -> NodeT:
    """Class decorator registering a node in the default registry.

    Args:
        node_class: The node class.

    Returns:
        The same class.
    """
    default_registry.register(node_class)
    return node_class


def load_node_catalog() -> NodeRegistry:
    """Import every node module so the default registry holds the full v1 catalog.

    Returns:
        The populated default registry.
    """
    for module in NODE_MODULES:
        importlib.import_module(module)
    return default_registry
