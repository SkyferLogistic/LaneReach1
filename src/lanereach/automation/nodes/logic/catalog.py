"""Logic nodes: waits, branches, A/B splits and stop.

Apart from `wait_business_days` (which reads the recipient's country calendar) and
`wait_for_engagement` (which reads the tracked email) these nodes are pure functions of their
config and the step's facts.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from lanereach.ai.schemas import ReplyBucket
from lanereach.automation.conditions import (
    ConditionExpression,
    evaluate_condition,
    resolve_condition_field,
)
from lanereach.automation.engine.context import ExecutionContext
from lanereach.automation.engine.results import Continue, Fail, NodeResult, Stop, Wait
from lanereach.automation.nodes.base import BaseNode, NodeCategory, NodeConfig
from lanereach.automation.nodes.fields import HandleName
from lanereach.automation.nodes.registry import register_node
from lanereach.domain.messages.models import Message, MessageDirection
from lanereach.integrations.tracking.tokens import ENGAGEMENT_WAIT_PREFIX

OTHER_HANDLE = "other"


class WaitBusinessDaysConfig(NodeConfig):
    """How long to wait."""

    days: int = Field(ge=1, le=90, description="Business days in the recipient's country.")


@register_node
class WaitBusinessDaysNode(BaseNode[WaitBusinessDaysConfig]):
    """Waits a number of business days in the recipient's country."""

    node_type = "wait_business_days"
    category = NodeCategory.LOGIC
    display_name = "Wait business days"
    description = (
        "Waits the chosen number of business days, skipping the recipient country's weekends "
        "and public holidays."
    )
    config_model = WaitBusinessDaysConfig
    is_wait = True

    async def execute(
        self, context: ExecutionContext, config: WaitBusinessDaysConfig
    ) -> NodeResult:
        """Wait until the window opens `days` business days from today, recipient local time."""
        services = context.require_services()
        subject = context.require_subject()
        calendar = await services.calendars.get_calendar(subject.contact.country)
        zone = calendar.timezone
        if subject.contact.timezone:
            try:
                zone = ZoneInfo(subject.contact.timezone)
            except (ZoneInfoNotFoundError, ValueError):
                zone = calendar.timezone
        today = context.now.astimezone(zone).date()
        target = calendar.add_business_days(today, config.days)
        until = datetime.combine(target, subject.tenant.sending_window_start, tzinfo=zone)
        return Wait(until=until, reason=f"wait {config.days} business days ({calendar.country})")


class WaitUntilDateConfig(NodeConfig):
    """A fixed date, or a date stored in an enrollment variable."""

    until: AwareDatetime | None = Field(default=None, description="Fixed date and time.")
    from_variable: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]{0,63}$",
        description="Name of an enrollment variable holding an ISO datetime (for example a "
        "return date extracted from an out-of-office reply).",
    )

    @model_validator(mode="after")
    def _require_exactly_one_source(self) -> Self:
        if (self.until is None) == (self.from_variable is None):
            raise ValueError("set exactly one of until or from_variable")
        return self


@register_node
class WaitUntilDateNode(BaseNode[WaitUntilDateConfig]):
    """Waits until a date."""

    node_type = "wait_until_date"
    category = NodeCategory.LOGIC
    display_name = "Wait until date"
    description = "Waits until a fixed date, or a date stored on the enrollment."
    config_model = WaitUntilDateConfig
    is_wait = True

    async def execute(self, context: ExecutionContext, config: WaitUntilDateConfig) -> NodeResult:
        """Wait until the configured date, or continue if it has passed."""
        until = config.until
        if config.from_variable is not None:
            until = _read_datetime(context.variables.get(config.from_variable))
            if until is None:
                # Continuing would risk an early email; fail visibly instead.
                return Fail(
                    error=f"enrollment variable '{config.from_variable}' is not a datetime",
                    retryable=False,
                )
        assert until is not None  # noqa: S101 - guaranteed by the config validator
        if until <= context.now:
            return Continue()
        return Wait(until=until, reason="wait_until_date")


CLICKED_HANDLE = "clicked"
OPENED_HANDLE = "opened"
NO_ENGAGEMENT_HANDLE = "no_engagement"


class WaitForEngagementConfig(NodeConfig):
    """How long to watch the last email and what counts."""

    days: int = Field(
        default=3, ge=1, le=30, description="Business days to wait (recipient's country)."
    )
    wait_for: Literal["open", "click"] = Field(
        default="open",
        description="`open`: leave at the first open (or click). `click`: wait for a click "
        "and leave by `opened` only when the time is up.",
    )


@register_node
class WaitForEngagementNode(BaseNode[WaitForEngagementConfig]):
    """Waits for the contact to open or click the enrollment's last email."""

    node_type = "wait_for_engagement"
    category = NodeCategory.LOGIC
    display_name = "Wait for open or click"
    description = (
        "Watches the last email of this enrollment for up to N business days: `clicked`, "
        "`opened` or `no_engagement`. Opens by mail scanners are ignored; Apple Mail may count "
        "as opened. Plain-text emails are not tracked and end in `no_engagement`."
    )
    config_model = WaitForEngagementConfig
    output_handles = (CLICKED_HANDLE, OPENED_HANDLE, NO_ENGAGEMENT_HANDLE)
    is_wait = True

    async def execute(
        self, context: ExecutionContext, config: WaitForEngagementConfig
    ) -> NodeResult:
        """Leave by the engagement seen so far, or wait (woken early by an open or click)."""
        services = context.require_services()
        key = f"{ENGAGEMENT_WAIT_PREFIX}{context.node_id}"
        message = await services.session.scalar(
            select(Message)
            .where(
                Message.enrollment_id == context.enrollment_id,
                Message.direction == MessageDirection.OUTBOUND,
            )
            .order_by(Message.occurred_at.desc())
            .limit(1)
        )
        deadline = _read_datetime(context.variables.get(key))
        if deadline is None:
            deadline = await _business_days_later(context, config.days)
            context.variables[key] = deadline.isoformat()
        clicked = bool(message and message.first_clicked_at)
        opened = bool(message and message.first_opened_at)
        handle: str | None = None
        if clicked:
            handle = CLICKED_HANDLE
        elif opened and config.wait_for == "open":
            handle = OPENED_HANDLE
        elif message is None or message.tracking_id is None or context.now >= deadline:
            handle = OPENED_HANDLE if opened else NO_ENGAGEMENT_HANDLE
        if handle is not None:
            context.variables.pop(key, None)
            context.output["engagement"] = handle
            return Continue(handle)
        # handle=None: this node runs again at the deadline, or earlier when the tracking
        # endpoint sees an open or click and wakes the enrollment.
        return Wait(until=deadline, reason=f"wait for {config.wait_for}", handle=None)


async def _business_days_later(context: ExecutionContext, days: int) -> datetime:
    services = context.require_services()
    subject = context.require_subject()
    calendar = await services.calendars.get_calendar(subject.contact.country)
    zone = calendar.timezone
    if subject.contact.timezone:
        try:
            zone = ZoneInfo(subject.contact.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            zone = calendar.timezone
    local = context.now.astimezone(zone)
    target = calendar.add_business_days(local.date(), days)
    return datetime.combine(target, local.timetz().replace(tzinfo=None), tzinfo=zone)


class WaitUntilWeekdayConfig(NodeConfig):
    """When to continue."""

    weekdays: list[int] = Field(
        default_factory=lambda: [2],
        min_length=1,
        max_length=7,
        description="ISO weekdays: 1 = Monday ... 7 = Sunday.",
    )
    at: str = Field(
        default="09:30",
        pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$",
        description="Local time in the recipient's timezone (HH:MM).",
    )

    @model_validator(mode="after")
    def _check_days(self) -> Self:
        if any(day < 1 or day > 7 for day in self.weekdays):
            raise ValueError("weekdays are 1 (Monday) to 7 (Sunday)")
        return self


@register_node
class WaitUntilWeekdayNode(BaseNode[WaitUntilWeekdayConfig]):
    """Waits until the next chosen weekday and time, in the recipient's timezone."""

    node_type = "wait_until_weekday"
    category = NodeCategory.LOGIC
    display_name = "Wait until weekday"
    description = (
        "Waits until the next chosen weekday at a set local time (for example Tuesday 09:30 in "
        "the recipient's timezone), when forwarders plan the week's shipments."
    )
    config_model = WaitUntilWeekdayConfig
    is_wait = True

    async def execute(
        self, context: ExecutionContext, config: WaitUntilWeekdayConfig
    ) -> NodeResult:
        """Wait until the next matching moment (today counts if it is still ahead)."""
        zone = await _recipient_zone(context)
        local = context.now.astimezone(zone)
        hour, minute = (int(part) for part in config.at.split(":"))
        for offset in range(8):
            day = local.date() + timedelta(days=offset)
            moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
            if day.isoweekday() in config.weekdays and moment > local:
                return Wait(until=moment, reason=f"wait until {moment:%a %H:%M} ({zone.key})")
        return Continue()


class BranchOnEmailStatusConfig(NodeConfig):
    """No settings: the contact's stored verification result decides."""


@register_node
class BranchOnEmailStatusNode(BaseNode[BranchOnEmailStatusConfig]):
    """Branches on the contact's email verification result."""

    node_type = "branch_on_email_status"
    category = NodeCategory.LOGIC
    display_name = "Branch on email status"
    description = (
        "Routes by the last verification result: valid, risky (also catch-all), invalid or "
        "unknown (never verified). Use `Verify email` to check first."
    )
    config_model = BranchOnEmailStatusConfig
    output_handles = ("valid", "risky", "invalid", "unknown")

    async def execute(
        self, context: ExecutionContext, config: BranchOnEmailStatusConfig
    ) -> NodeResult:
        """Follow the output of the stored status."""
        status = context.require_subject().contact.email_status
        if status == "catch_all":
            return Continue("risky")
        return Continue(status if status in {"valid", "risky", "invalid"} else "unknown")


async def _recipient_zone(context: ExecutionContext) -> ZoneInfo:
    services = context.require_services()
    subject = context.require_subject()
    calendar = await services.calendars.get_calendar(subject.contact.country)
    if subject.contact.timezone:
        try:
            return ZoneInfo(subject.contact.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return calendar.timezone


def _read_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else None
    return None


class IfConditionConfig(NodeConfig):
    """The condition to test."""

    condition: ConditionExpression


@register_node
class IfConditionNode(BaseNode[IfConditionConfig]):
    """Branches on a condition."""

    node_type = "if_condition"
    category = NodeCategory.LOGIC
    display_name = "If / else"
    description = "Follows `true` when the condition holds, otherwise `false`."
    config_model = IfConditionConfig
    output_handles = ("true", "false")

    async def execute(self, context: ExecutionContext, config: IfConditionConfig) -> NodeResult:
        """Evaluate the condition against the step's facts."""
        return Continue("true" if evaluate_condition(config.condition, context.facts) else "false")


class BranchOnReplyBucketConfig(NodeConfig):
    """Which buckets get their own branch."""

    buckets: list[ReplyBucket] = Field(
        default_factory=lambda: list(ReplyBucket),
        min_length=1,
        description="One output per bucket listed, plus `other` for everything else.",
    )

    @model_validator(mode="after")
    def _reject_duplicates(self) -> Self:
        if len(set(self.buckets)) != len(self.buckets):
            raise ValueError("buckets must not repeat")
        return self


@register_node
class BranchOnReplyBucketNode(BaseNode[BranchOnReplyBucketConfig]):
    """Branches on the classified bucket of the latest reply."""

    node_type = "branch_on_reply_bucket"
    category = NodeCategory.LOGIC
    display_name = "Branch on reply"
    description = "Routes by the latest reply's bucket; unlisted buckets go to `other`."
    config_model = BranchOnReplyBucketConfig
    output_handles = (OTHER_HANDLE,)
    has_dynamic_handles = True

    @classmethod
    def handles_for(cls, config: NodeConfig) -> tuple[str, ...]:
        """One handle per configured bucket plus `other`."""
        assert isinstance(config, BranchOnReplyBucketConfig)  # noqa: S101
        return (*(bucket.value for bucket in config.buckets), OTHER_HANDLE)

    async def execute(
        self, context: ExecutionContext, config: BranchOnReplyBucketConfig
    ) -> NodeResult:
        """Follow the handle of the reply's bucket, or `other`."""
        bucket = context.facts.get("reply.bucket")
        value = bucket.value if isinstance(bucket, ReplyBucket) else bucket
        listed = {item.value for item in config.buckets}
        return Continue(value if isinstance(value, str) and value in listed else OTHER_HANDLE)


class SplitVariant(BaseModel):
    """One arm of an A/B split."""

    model_config = ConfigDict(extra="forbid")

    name: HandleName
    percentage: int = Field(ge=1, le=99)


class ABSplitConfig(NodeConfig):
    """Variants and their share of contacts."""

    variants: list[SplitVariant] = Field(
        min_length=2,
        max_length=5,
        examples=[[{"name": "a", "percentage": 50}, {"name": "b", "percentage": 50}]],
    )

    @model_validator(mode="after")
    def _check_variants(self) -> Self:
        names = [variant.name for variant in self.variants]
        if len(set(names)) != len(names):
            raise ValueError("variant names must be unique")
        if sum(variant.percentage for variant in self.variants) != 100:
            raise ValueError("variant percentages must add up to 100")
        return self


@register_node
class ABSplitNode(BaseNode[ABSplitConfig]):
    """Splits contacts deterministically between variants."""

    node_type = "ab_split"
    category = NodeCategory.LOGIC
    display_name = "A/B split"
    description = (
        "Sends each contact down one variant by percentage. The choice is deterministic, so a "
        "retried step always picks the same variant."
    )
    config_model = ABSplitConfig
    output_handles = ()
    has_dynamic_handles = True

    @classmethod
    def handles_for(cls, config: NodeConfig) -> tuple[str, ...]:
        """One handle per variant."""
        assert isinstance(config, ABSplitConfig)  # noqa: S101
        return tuple(variant.name for variant in config.variants)

    async def execute(self, context: ExecutionContext, config: ABSplitConfig) -> NodeResult:
        """Pick a variant from a hash of the enrollment and node IDs."""
        return Continue(choose_split_variant(config, f"{context.enrollment_id}:{context.node_id}"))


def choose_split_variant(config: ABSplitConfig, seed: str) -> str:
    """Map a seed onto a variant by percentage.

    Hashing (rather than random choice) makes the result stable across retries and replays.

    Args:
        config: The split configuration.
        seed: Stable identifier, `enrollment_id:node_id`.

    Returns:
        The chosen variant name.
    """
    bucket = int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big") % 100
    threshold = 0
    for variant in config.variants:
        threshold += variant.percentage
        if bucket < threshold:
            return variant.name
    return config.variants[-1].name  # pragma: no cover - percentages always sum to 100


class StopConfig(NodeConfig):
    """Optional reason recorded on the enrollment."""

    reason: str | None = Field(default=None, max_length=200)


@register_node
class StopNode(BaseNode[StopConfig]):
    """Ends the workflow for this contact."""

    node_type = "stop"
    category = NodeCategory.LOGIC
    display_name = "Stop"
    description = "Ends the workflow for this contact."
    config_model = StopConfig
    output_handles = ()
    is_terminal = True

    async def execute(self, context: ExecutionContext, config: StopConfig) -> NodeResult:
        """End the enrollment."""
        return Stop(reason=config.reason or "workflow completed")


class SwitchCase(BaseModel):
    """One branch of a switch: the values that go down it."""

    model_config = ConfigDict(extra="forbid")

    handle: HandleName = Field(description="Name of the output, for example `arabic`.")
    values: list[str] = Field(
        min_length=1,
        max_length=50,
        description="Values of the field that take this branch (case-insensitive).",
        examples=[["AE", "SA", "QA"]],
    )


class SwitchOnFieldConfig(NodeConfig):
    """The field to look at and one branch per group of values."""

    field: str = Field(
        description="A condition field, for example `contact.country` or `contact.network`.",
        examples=["contact.country"],
    )
    cases: list[SwitchCase] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if resolve_condition_field(self.field) is None:
            raise ValueError(f"unknown field '{self.field}'")
        handles = [case.handle for case in self.cases]
        if len(set(handles)) != len(handles):
            raise ValueError("case names must not repeat")
        if OTHER_HANDLE in handles:
            raise ValueError(f"'{OTHER_HANDLE}' is reserved for values no case lists")
        return self


@register_node
class SwitchOnFieldNode(BaseNode[SwitchOnFieldConfig]):
    """Branches many ways on one field (country groups, networks, languages...)."""

    node_type = "switch_on_field"
    category = NodeCategory.LOGIC
    display_name = "Switch on field"
    description = (
        "Routes by the value of a field, e.g. the Gulf countries one way, Europe another; "
        "everything else goes to `other`."
    )
    config_model = SwitchOnFieldConfig
    output_handles = (OTHER_HANDLE,)
    has_dynamic_handles = True

    @classmethod
    def handles_for(cls, config: NodeConfig) -> tuple[str, ...]:
        """One handle per case plus `other`."""
        assert isinstance(config, SwitchOnFieldConfig)  # noqa: S101
        return (*(case.handle for case in config.cases), OTHER_HANDLE)

    async def execute(self, context: ExecutionContext, config: SwitchOnFieldConfig) -> NodeResult:
        """Follow the first case listing the field's value (lists match on any element)."""
        return Continue(choose_switch_case(config, context.facts.get(config.field)))


def choose_switch_case(config: SwitchOnFieldConfig, value: object) -> str:
    """The handle a value takes (`other` when no case lists it).

    Args:
        config: The switch.
        value: The field's value (enums compare by value; lists match on any element).

    Returns:
        The handle.
    """
    items = value if isinstance(value, list | tuple | set) else [value]
    present = {str(getattr(item, "value", item)).casefold() for item in items if item is not None}
    for case in config.cases:
        if present & {listed.casefold() for listed in case.values}:
            return case.handle
    return OTHER_HANDLE
