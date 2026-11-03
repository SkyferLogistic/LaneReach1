"""Safe condition DSL used by `if_condition` nodes and trigger filters.

Conditions are JSON, never code (no `eval`)::

    {"all": [
        {"field": "contact.country", "op": "in", "value": ["AE", "SA"]},
        {"not": {"field": "contact.tags", "op": "contains", "value": "partner"}}
    ]}

* **Comparisons** `{field, op, value}` with operators `eq`, `ne`, `in`, `not_in`, `gt`, `gte`, `lt`,
  `lte`, `contains`, `exists`.
* **Combinators** `all`, `any`, `not`.
* **Fields are whitelisted** (`CONDITION_FIELDS`) and typed; each type allows only sensible
  operators and value shapes, so mistakes are caught when a workflow is saved, not when it runs.
  Free-form enrollment variables are addressed as `enrollment.variables.<name>`.
* **Limits** (depth and number of comparisons) keep evaluation cheap no matter what is submitted.

Evaluation semantics: string comparisons are case-insensitive; a missing value (None) makes
`ne` and `not_in` true and every other operator false (except `exists`); a value of the wrong
runtime type never raises — the comparison is simply false.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    model_validator,
)

from lanereach.ai.schemas import (
    ContainerType,
    Incoterm,
    ReplyBucket,
    ShipmentFrequency,
    ShippingMode,
)
from lanereach.domain.contacts.models import ContactStage

MAX_CONDITION_DEPTH = 5
MAX_CONDITION_COMPARISONS = 50
MAX_IN_VALUES = 100
VARIABLE_PREFIX = "enrollment.variables."
_VARIABLE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ConditionOperator(StrEnum):
    """Comparison operators."""

    EQ = "eq"
    NE = "ne"
    IN = "in"
    NOT_IN = "not_in"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    CONTAINS = "contains"
    EXISTS = "exists"


class ConditionFieldType(StrEnum):
    """Value type of a condition field."""

    STRING = "string"
    ENUM = "enum"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    STRING_LIST = "string_list"
    ANY = "any"


_ORDERING = {
    ConditionOperator.GT,
    ConditionOperator.GTE,
    ConditionOperator.LT,
    ConditionOperator.LTE,
}
_EQUALITY = {ConditionOperator.EQ, ConditionOperator.NE}
_MEMBERSHIP = {ConditionOperator.IN, ConditionOperator.NOT_IN}

OPERATORS_BY_TYPE: dict[ConditionFieldType, frozenset[ConditionOperator]] = {
    ConditionFieldType.STRING: frozenset(
        {*_EQUALITY, *_MEMBERSHIP, ConditionOperator.CONTAINS, ConditionOperator.EXISTS}
    ),
    ConditionFieldType.ENUM: frozenset({*_EQUALITY, *_MEMBERSHIP, ConditionOperator.EXISTS}),
    ConditionFieldType.NUMBER: frozenset(
        {*_EQUALITY, *_MEMBERSHIP, *_ORDERING, ConditionOperator.EXISTS}
    ),
    ConditionFieldType.BOOLEAN: frozenset({*_EQUALITY, ConditionOperator.EXISTS}),
    ConditionFieldType.DATETIME: frozenset({*_EQUALITY, *_ORDERING, ConditionOperator.EXISTS}),
    ConditionFieldType.STRING_LIST: frozenset(
        {ConditionOperator.CONTAINS, ConditionOperator.EXISTS}
    ),
    ConditionFieldType.ANY: frozenset(ConditionOperator),
}


@dataclass(frozen=True, slots=True)
class ConditionField:
    """A field conditions may reference."""

    name: str
    type: ConditionFieldType
    description: str
    enum_values: tuple[str, ...] = ()

    @property
    def scope(self) -> str:
        """The field's namespace (`contact`, `company`, `reply`, `lead` or `enrollment`)."""
        return self.name.split(".", 1)[0]


def _enum_field(name: str, description: str, values: type[StrEnum]) -> ConditionField:
    return ConditionField(
        name, ConditionFieldType.ENUM, description, tuple(item.value for item in values)
    )


_S, _N, _B, _D, _L = (
    ConditionFieldType.STRING,
    ConditionFieldType.NUMBER,
    ConditionFieldType.BOOLEAN,
    ConditionFieldType.DATETIME,
    ConditionFieldType.STRING_LIST,
)

CONDITION_FIELDS: dict[str, ConditionField] = {
    field.name: field
    for field in (
        ConditionField("contact.email", _S, "Contact email address"),
        ConditionField("contact.email_domain", _S, "Domain part of the contact's email"),
        ConditionField("contact.first_name", _S, "Contact first name"),
        ConditionField("contact.last_name", _S, "Contact last name"),
        ConditionField("contact.job_title", _S, "Contact job title"),
        ConditionField("contact.country", _S, "Contact country (ISO 3166-1 alpha-2)"),
        ConditionField("contact.city", _S, "Contact city"),
        ConditionField("contact.language", _S, "Preferred language (ISO 639-1)"),
        _enum_field("contact.stage", "Funnel stage", ContactStage),
        ConditionField("contact.tags", _L, "Tags on the contact"),
        ConditionField("contact.owner", _S, "Email of the contact owner"),
        ConditionField("contact.network", _S, "Forwarder network (WCA, JCtrans...)"),
        ConditionField("contact.created_at", _D, "When the contact was created"),
        ConditionField("company.name", _S, "Company name"),
        ConditionField("company.domain", _S, "Company web/email domain"),
        ConditionField("company.country", _S, "Company head-office country (ISO 3166-1 alpha-2)"),
        ConditionField("company.industry", _S, "Company industry"),
        ConditionField("company.is_frozen", _B, "Company frozen after a complaint or block"),
        ConditionField("company.branch_count", _N, "Number of company branches"),
        _enum_field("reply.bucket", "Classified reply bucket", ReplyBucket),
        ConditionField("reply.confidence", _N, "Classification confidence (0-1)"),
        ConditionField("reply.language", _S, "Detected reply language (ISO 639-1)"),
        ConditionField("reply.needs_human", _B, "Classifier asked for human review"),
        _enum_field("lead.mode", "Shipment mode", ShippingMode),
        ConditionField("lead.origin_country", _S, "Origin country (ISO 3166-1 alpha-2)"),
        ConditionField("lead.destination_country", _S, "Destination country (ISO alpha-2)"),
        _enum_field("lead.container_type", "Container type", ContainerType),
        ConditionField("lead.container_quantity", _N, "Number of containers"),
        ConditionField("lead.weight_kg", _N, "Gross weight in kg"),
        ConditionField("lead.volume_cbm", _N, "Volume in cubic metres"),
        ConditionField("lead.commodity", _S, "Commodity description"),
        _enum_field("lead.incoterm", "Incoterm (2020)", Incoterm),
        _enum_field("lead.frequency", "Shipment frequency", ShipmentFrequency),
        ConditionField("lead.is_hazardous", _B, "Dangerous goods"),
    )
}


def resolve_condition_field(name: str) -> ConditionField | None:
    """Look up a whitelisted field, including `enrollment.variables.<name>`.

    Args:
        name: Dotted field name.

    Returns:
        The field definition, or None if the field is not allowed.
    """
    if name.startswith(VARIABLE_PREFIX):
        variable = name.removeprefix(VARIABLE_PREFIX)
        if _VARIABLE_NAME.fullmatch(variable):
            return ConditionField(name, ConditionFieldType.ANY, "Enrollment variable")
        return None
    return CONDITION_FIELDS.get(name)


# --- Models ------------------------------------------------------------------------------------


class Comparison(BaseModel):
    """`{field, op, value}` — compares one field with a literal."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(max_length=120)
    op: ConditionOperator
    value: Any = None

    @model_validator(mode="after")
    def _check_field_operator_and_value(self) -> Comparison:
        definition = resolve_condition_field(self.field)
        if definition is None:
            raise ValueError(f"unknown field '{self.field}'")
        if self.op not in OPERATORS_BY_TYPE[definition.type]:
            allowed = ", ".join(sorted(OPERATORS_BY_TYPE[definition.type]))
            raise ValueError(
                f"operator '{self.op}' is not allowed on {definition.type} field "
                f"'{self.field}' (allowed: {allowed})"
            )
        _check_value(definition, self.op, self.value)
        return self


class AllCondition(BaseModel):
    """True when every child is true."""

    model_config = ConfigDict(extra="forbid")

    all: list[Condition] = Field(min_length=1, max_length=MAX_CONDITION_COMPARISONS)


class AnyCondition(BaseModel):
    """True when at least one child is true."""

    model_config = ConfigDict(extra="forbid")

    any: list[Condition] = Field(min_length=1, max_length=MAX_CONDITION_COMPARISONS)


class NotCondition(BaseModel):
    """Negates its child."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)

    not_: Condition = Field(alias="not")


def _condition_kind(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("all", "any", "not"):
            if key in value:
                return key
        return "comparison"
    if isinstance(value, AllCondition):
        return "all"
    if isinstance(value, AnyCondition):
        return "any"
    if isinstance(value, NotCondition):
        return "not"
    return "comparison"


Condition = Annotated[
    Annotated[Comparison, Tag("comparison")]
    | Annotated[AllCondition, Tag("all")]
    | Annotated[AnyCondition, Tag("any")]
    | Annotated[NotCondition, Tag("not")],
    Discriminator(_condition_kind),
]

for _model in (AllCondition, AnyCondition, NotCondition):
    _model.model_rebuild()


def _check_condition_limits(condition: Condition) -> Condition:
    depth, comparisons = _measure(condition, 1)
    if depth > MAX_CONDITION_DEPTH:
        raise ValueError(f"condition is nested deeper than {MAX_CONDITION_DEPTH} levels")
    if comparisons > MAX_CONDITION_COMPARISONS:
        raise ValueError(f"condition has more than {MAX_CONDITION_COMPARISONS} comparisons")
    return condition


ConditionExpression = Annotated[Condition, AfterValidator(_check_condition_limits)]
"""A condition that also respects the depth and size limits. Use this in node configs."""


def _measure(condition: Condition, depth: int) -> tuple[int, int]:
    if isinstance(condition, Comparison):
        return depth, 1
    children = (
        [condition.not_]
        if isinstance(condition, NotCondition)
        else (condition.all if isinstance(condition, AllCondition) else condition.any)
    )
    measured = [_measure(child, depth + 1) for child in children]
    return max(item[0] for item in measured), sum(item[1] for item in measured)


def iter_condition_fields(condition: Condition) -> list[str]:
    """Return every field name a condition references (used for validator warnings).

    Args:
        condition: A parsed condition.

    Returns:
        Field names in document order.
    """
    if isinstance(condition, Comparison):
        return [condition.field]
    if isinstance(condition, NotCondition):
        return iter_condition_fields(condition.not_)
    children = condition.all if isinstance(condition, AllCondition) else condition.any
    return [name for child in children for name in iter_condition_fields(child)]


# --- Value validation --------------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else None
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def _check_scalar(definition: ConditionField, value: Any) -> None:
    kind = definition.type
    if kind is ConditionFieldType.STRING and not isinstance(value, str):
        raise ValueError(f"'{definition.name}' expects a string")
    if kind is ConditionFieldType.ENUM:
        allowed = {item.casefold() for item in definition.enum_values}
        if not isinstance(value, str) or value.casefold() not in allowed:
            raise ValueError(
                f"'{definition.name}' expects one of: {', '.join(definition.enum_values)}"
            )
    if kind is ConditionFieldType.NUMBER and not _is_number(value):
        raise ValueError(f"'{definition.name}' expects a number")
    if kind is ConditionFieldType.BOOLEAN and not isinstance(value, bool):
        raise ValueError(f"'{definition.name}' expects true or false")
    if kind is ConditionFieldType.DATETIME and _parse_datetime(value) is None:
        raise ValueError(f"'{definition.name}' expects an ISO 8601 datetime with timezone")
    if kind is ConditionFieldType.ANY and not (
        value is None or isinstance(value, str | bool) or _is_number(value)
    ):
        raise ValueError(f"'{definition.name}' expects a string, number or boolean")


def _check_value(definition: ConditionField, op: ConditionOperator, value: Any) -> None:
    if op is ConditionOperator.EXISTS:
        if value is not None and not isinstance(value, bool):
            raise ValueError("'exists' takes no value, or true/false")
        return
    if op in _MEMBERSHIP:
        if not isinstance(value, list) or not value or len(value) > MAX_IN_VALUES:
            raise ValueError(f"'{op}' expects a list of 1-{MAX_IN_VALUES} values")
        for item in value:
            _check_scalar(definition, item)
        return
    if op is ConditionOperator.CONTAINS:
        if not isinstance(value, str) or not value:
            raise ValueError("'contains' expects a non-empty string")
        return
    if value is None:
        raise ValueError(f"'{op}' requires a value")
    if op in _ORDERING and definition.type is ConditionFieldType.ANY:
        if not (_is_number(value) or _parse_datetime(value) is not None):
            raise ValueError(f"'{op}' on a variable expects a number or datetime")
        return
    _check_scalar(definition, value)


# --- Evaluation --------------------------------------------------------------------------------


def evaluate_condition(condition: Condition, facts: Mapping[str, Any]) -> bool:
    """Evaluate a condition against facts about the current contact/enrollment.

    Args:
        condition: A parsed (and therefore validated) condition.
        facts: Flat mapping of field name to value, for example
            `{"contact.country": "AE", "reply.bucket": "rate_query"}`.

    Returns:
        Whether the condition holds.
    """
    if isinstance(condition, AllCondition):
        return all(evaluate_condition(child, facts) for child in condition.all)
    if isinstance(condition, AnyCondition):
        return any(evaluate_condition(child, facts) for child in condition.any)
    if isinstance(condition, NotCondition):
        return not evaluate_condition(condition.not_, facts)
    return _evaluate_comparison(condition, facts.get(condition.field))


def _normalise(value: Any) -> Any:
    if isinstance(value, str):
        parsed = _parse_datetime(value)
        return parsed if parsed is not None else value.casefold()
    if isinstance(value, StrEnum):
        return value.value.casefold()
    return value


def _evaluate_comparison(comparison: Comparison, actual: Any) -> bool:
    op = comparison.op
    if op is ConditionOperator.EXISTS:
        wanted = True if comparison.value is None else bool(comparison.value)
        present = actual is not None and actual != [] and actual != ""
        return present is wanted
    if actual is None:
        return op in {ConditionOperator.NE, ConditionOperator.NOT_IN}

    if op is ConditionOperator.CONTAINS:
        needle = str(comparison.value).casefold()
        if isinstance(actual, str):
            return needle in actual.casefold()
        if isinstance(actual, list | tuple | set | frozenset):
            return any(isinstance(item, str) and item.casefold() == needle for item in actual)
        return False

    left = _normalise(actual)
    if op in _MEMBERSHIP:
        members = [_normalise(item) for item in comparison.value]
        return (left in members) is (op is ConditionOperator.IN)

    right = _normalise(comparison.value)
    if op is ConditionOperator.EQ:
        return bool(left == right)
    if op is ConditionOperator.NE:
        return bool(left != right)
    return _compare_ordered(op, left, right)


def _compare_ordered(op: ConditionOperator, left: Any, right: Any) -> bool:
    comparable = (_is_number(left) and _is_number(right)) or (
        isinstance(left, datetime) and isinstance(right, datetime)
    )
    if not comparable:
        return False
    result: bool
    if op is ConditionOperator.GT:
        result = left > right
    elif op is ConditionOperator.GTE:
        result = left >= right
    elif op is ConditionOperator.LT:
        result = left < right
    else:
        result = left <= right
    return result
