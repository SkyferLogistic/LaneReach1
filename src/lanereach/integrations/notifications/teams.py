"""Microsoft Teams incoming-webhook notifier."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx
import structlog

from lanereach.integrations.notifications.notifier import (
    TEAMS_CHANNEL_PREFIX,
    NotificationMessage,
    Notifier,
)

logger = structlog.get_logger(__name__)


def build_teams_card(message: NotificationMessage) -> dict[str, Any]:
    """Render a notification as a Teams Adaptive Card message.

    Args:
        message: The notification.

    Returns:
        The JSON body for the incoming webhook.
    """
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {"type": "TextBlock", "text": message.subject, "weight": "Bolder"},
                        {"type": "TextBlock", "text": message.body, "wrap": True},
                    ],
                },
            }
        ],
    }


class TeamsWebhookNotifier:
    """Posts `teams:<channel>` notifications to that channel's webhook, then records them.

    Delivery failures are logged, never raised: a Teams outage must not fail an enrollment
    step that already sent an email.

    Args:
        http_client: Outbound HTTP client.
        webhook_urls: Channel name to incoming-webhook URL.
        record: Notifier that records every notification (usually `RecordingNotifier`).
    """

    def __init__(
        self, http_client: httpx.AsyncClient, webhook_urls: Mapping[str, str], record: Notifier
    ) -> None:
        self._http = http_client
        self._urls = dict(webhook_urls)
        self._record = record

    async def notify(self, message: NotificationMessage) -> None:
        """Post to Teams when a webhook is configured for the channel, and record it."""
        if message.channel.startswith(TEAMS_CHANNEL_PREFIX):
            channel = message.channel.removeprefix(TEAMS_CHANNEL_PREFIX)
            url = self._urls.get(channel)
            if url:
                try:
                    response = await self._http.post(url, json=build_teams_card(message))
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    logger.warning(
                        "teams_delivery_failed", channel=channel, error=type(exc).__name__
                    )
        await self._record.notify(message)
