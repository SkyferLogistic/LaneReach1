"""Node catalog endpoints: what the workflow canvas renders in its palette and forms."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from lanereach.auth.dependencies import require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.automation.conditions import (
    CONDITION_FIELDS,
    OPERATORS_BY_TYPE,
    VARIABLE_PREFIX,
    ConditionFieldType,
)
from lanereach.automation.nodes.base import BaseNode, NodeCategory
from lanereach.automation.nodes.registry import NodeRegistry
from lanereach.automation.nodes.triggers.catalog import trigger_event_type
from lanereach.core.dependencies import ContainerDependency
from lanereach.core.errors import NotFoundError, ProblemDocument
from lanereach.core.schemas import ResponseModel

router = APIRouter(prefix="/automation", tags=["Automation"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]
_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ProblemDocument},
    403: {"model": ProblemDocument},
}


class NodeTypeResponse(ResponseModel):
    """A node type as shown in the canvas palette."""

    node_type: str
    category: NodeCategory
    display_name: str
    description: str
    output_handles: list[str]
    has_dynamic_handles: bool
    is_terminal: bool
    is_cold_send: bool
    is_wait: bool
    event_type: str | None
    config_schema: dict[str, Any]


class ConditionFieldResponse(ResponseModel):
    """A field the condition builder may offer."""

    field: str
    type: ConditionFieldType
    description: str
    operators: list[str]
    enum_values: list[str]


class ConditionFieldsResponse(ResponseModel):
    """Every whitelisted condition field."""

    fields: list[ConditionFieldResponse]
    variable_prefix: str
    variable_operators: list[str]


def _describe_node(node_class: type[BaseNode[Any]]) -> NodeTypeResponse:
    return NodeTypeResponse(
        node_type=node_class.node_type,
        category=node_class.category,
        display_name=node_class.display_name,
        description=node_class.description,
        output_handles=list(node_class.output_handles),
        has_dynamic_handles=node_class.has_dynamic_handles,
        is_terminal=node_class.is_terminal,
        is_cold_send=node_class.is_cold_send,
        is_wait=node_class.is_wait,
        event_type=trigger_event_type(node_class),
        config_schema=node_class.config_model.model_json_schema(),
    )


def _registry(container: ContainerDependency) -> NodeRegistry:
    return container.node_registry


@router.get(
    "/nodes",
    response_model=list[NodeTypeResponse],
    summary="List workflow node types",
    description="Every node with category, description, output handles and the JSON Schema of "
    "its config. Nodes with `has_dynamic_handles` derive handles from their config "
    "(for example one per A/B variant).",
    responses=_ERRORS,
)
async def list_node_types(
    _principal: Reader, registry: Annotated[NodeRegistry, Depends(_registry)]
) -> list[NodeTypeResponse]:
    """List node types in palette order."""
    return [_describe_node(node_class) for node_class in registry.all_nodes()]


@router.get(
    "/nodes/{node_type}",
    response_model=NodeTypeResponse,
    summary="Describe one workflow node type",
    responses={**_ERRORS, 404: {"model": ProblemDocument}},
)
async def read_node_type(
    node_type: str, _principal: Reader, registry: Annotated[NodeRegistry, Depends(_registry)]
) -> NodeTypeResponse:
    """Describe a node type."""
    node_class = registry.find(node_type)
    if node_class is None:
        raise NotFoundError(f"Unknown node type '{node_type}'.")
    return _describe_node(node_class)


@router.get(
    "/condition-fields",
    response_model=ConditionFieldsResponse,
    summary="List fields usable in conditions",
    description="The whitelist the condition DSL accepts, with allowed operators per field.",
    responses=_ERRORS,
)
async def list_condition_fields(_principal: Reader) -> ConditionFieldsResponse:
    """List condition fields."""
    return ConditionFieldsResponse(
        fields=[
            ConditionFieldResponse(
                field=field.name,
                type=field.type,
                description=field.description,
                operators=sorted(OPERATORS_BY_TYPE[field.type]),
                enum_values=list(field.enum_values),
            )
            for field in CONDITION_FIELDS.values()
        ],
        variable_prefix=VARIABLE_PREFIX,
        variable_operators=sorted(OPERATORS_BY_TYPE[ConditionFieldType.ANY]),
    )
