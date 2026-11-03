"""In-memory notifier for unit tests."""

from __future__ import annotations

from lanereach.integrations.notifications.notifier import NotificationMessage


class FakeNotifier:
    """Keeps notifications in a list so tests can assert on them."""

    def __init__(self) -> None:
        self.sent: list[NotificationMessage] = []

    async def notify(self, message: NotificationMessage) -> None:
        """Remember the notification."""
        self.sent.append(message)
