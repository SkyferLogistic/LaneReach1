"""Redis-backed rate limits (fixed one-minute windows) for expensive endpoints.

AI endpoints call a paid LLM, so each tenant and each caller (user or API key) gets a request
budget per minute. Limits are checked per tenant *and* per caller: one noisy integration key
cannot use up the whole tenant's budget, and many keys together cannot exceed the tenant's.

If Redis is unreachable the request is allowed and a warning is logged: rate limiting protects
cost, and an outage of the limiter should not take the AI features down with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from lanereach.core.errors import RateLimitedError

logger = structlog.get_logger(__name__)

WINDOW_SECONDS = 60
_KEY_PREFIX = "lanereach:ratelimit"


@dataclass(frozen=True)
class RateLimitDecision:
    """Outcome of counting one request against a limit."""

    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int


class RedisRateLimiter:
    """Counts requests per key and window in Redis.

    Args:
        redis: Redis client.
    """

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def count_request(self, key: str, *, limit: int, now: datetime) -> RateLimitDecision:
        """Count one request for `key` in the current window.

        Args:
            key: What is limited, for example `ai:tenant:<id>`.
            limit: Requests allowed per window.
            now: Current time (from the `Clock`).

        Returns:
            Whether the request is within the limit.
        """
        epoch = int(now.timestamp())
        window = epoch // WINDOW_SECONDS
        redis_key = f"{_KEY_PREFIX}:{key}:{window}"
        async with self._redis.pipeline(transaction=True) as pipeline:
            pipeline.incr(redis_key)
            pipeline.expire(redis_key, WINDOW_SECONDS * 2)
            count, _ = await pipeline.execute()
        return RateLimitDecision(
            allowed=int(count) <= limit,
            limit=limit,
            remaining=max(0, limit - int(count)),
            retry_after_seconds=WINDOW_SECONDS - epoch % WINDOW_SECONDS,
        )


async def enforce_rate_limits(
    limiter: RedisRateLimiter, limits: list[tuple[str, int]], *, now: datetime
) -> None:
    """Count the request against every limit; raise if any is exceeded.

    Args:
        limiter: The limiter.
        limits: `(key, requests per minute)` pairs; a limit of 0 disables that key.
        now: Current time.

    Raises:
        RateLimitedError: With a `Retry-After` header, when a limit is exceeded.
    """
    for key, limit in limits:
        if limit <= 0:
            continue
        try:
            decision = await limiter.count_request(key, limit=limit, now=now)
        except (RedisError, OSError) as exc:
            logger.warning("rate_limit_unavailable", error=type(exc).__name__)
            return
        if not decision.allowed:
            scope = key.split(":", 2)[1]
            raise RateLimitedError(
                f"Too many AI requests for this {scope}: the limit is {limit} per minute.",
                headers={
                    "Retry-After": str(decision.retry_after_seconds),
                    "X-RateLimit-Limit": str(limit),
                },
            )
