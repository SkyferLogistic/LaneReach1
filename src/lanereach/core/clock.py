"""Time source abstraction.

Business rules in LaneReach are time-heavy (sending windows, business days, frequency caps,
waits). Reading the time through a `Clock` lets tests and the workflow test-run endpoint
simulate time deterministically instead of patching `datetime`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """Source of the current time. Implementations always return timezone-aware UTC."""

    def now(self) -> datetime:
        """Return the current instant as an aware UTC datetime."""
        ...


class SystemClock:
    """Clock backed by the operating system time."""

    def now(self) -> datetime:
        """Return the current system time in UTC.

        Returns:
            The current aware UTC datetime.
        """
        return datetime.now(UTC)


class ManualClock:
    """Clock whose time only moves when told to.

    Used by tests and by simulations (workflow test-runs) that need to fast-forward through
    waits without sleeping.

    Args:
        start: The initial instant. Must be timezone-aware.

    Raises:
        ValueError: If `start` is naive.
    """

    def __init__(self, start: datetime) -> None:
        _require_aware(start)
        self._current = start.astimezone(UTC)

    def now(self) -> datetime:
        """Return the simulated current time.

        Returns:
            The simulated aware UTC datetime.
        """
        return self._current

    def advance(self, delta: timedelta) -> datetime:
        """Move the clock forward.

        Args:
            delta: How far to move. Must not be negative; time never goes backwards.

        Returns:
            The new current time.

        Raises:
            ValueError: If `delta` is negative.
        """
        if delta < timedelta(0):
            raise ValueError("ManualClock cannot move backwards")
        self._current += delta
        return self._current

    def set(self, instant: datetime) -> None:
        """Jump to a specific instant.

        Args:
            instant: The new current time. Must be timezone-aware.

        Raises:
            ValueError: If `instant` is naive.
        """
        _require_aware(instant)
        self._current = instant.astimezone(UTC)


def _require_aware(instant: datetime) -> None:
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
