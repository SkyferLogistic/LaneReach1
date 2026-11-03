"""`Idempotency-Key` support for side-effecting POST endpoints.

How it works:

1. `IdempotencyGuard.claim()` inserts a row for (tenant, key) with `ON CONFLICT DO NOTHING`
   *inside the request's tenant transaction*.
2. If the insert wins, the endpoint performs its work and calls `complete()` to store the
   response on the same row. The row and the side effects commit (or roll back) together, so
   a key can never be recorded for work that did not happen.
3. A concurrent request with the same key blocks on the unique index until the first
   transaction ends, then finds the completed row and replays its response.
4. The same key with a different method, path or body is rejected with 422.

Requests without the header are processed normally.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Integer, String, UniqueConstraint, delete, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.core.clock import Clock
from lanereach.core.database import Base, CreatedAtMixin, TenantScopedMixin, UUIDPrimaryKeyMixin
from lanereach.core.errors import ConflictError, IdempotencyKeyReusedError, LaneReachError
from lanereach.core.tenancy import require_session_tenant_id

IDEMPOTENCY_HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotent-Replayed"
_KEY_PATTERN = re.compile(r"^[\x21-\x7e]{1,255}$")


class IdempotencyRecord(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """The stored outcome of a request made with an `Idempotency-Key`."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)

    key: Mapped[str] = mapped_column(String(255))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[Any | None] = mapped_column(JSONB)
    expires_at: Mapped[datetime]


class InvalidIdempotencyKeyError(LaneReachError):
    """The `Idempotency-Key` header value is not acceptable."""

    status_code = 400
    problem_type = "invalid-idempotency-key"
    title = "Invalid Idempotency-Key header"


def fingerprint_request(method: str, path: str, body: bytes) -> str:
    """Hash the parts of a request that must match for a key to be replayed.

    JSON bodies are canonicalised (sorted keys, no whitespace) so formatting differences do
    not count as a different request.

    Args:
        method: HTTP method.
        path: URL path.
        body: Raw request body.

    Returns:
        SHA-256 hex digest.
    """
    try:
        canonical = json.dumps(json.loads(body), sort_keys=True, separators=(",", ":")).encode()
    except (ValueError, UnicodeDecodeError):
        canonical = body
    digest = hashlib.sha256()
    digest.update(method.upper().encode())
    digest.update(b"\n")
    digest.update(path.encode())
    digest.update(b"\n")
    digest.update(canonical)
    return digest.hexdigest()


class IdempotencyGuard:
    """Per-request helper that replays or records responses for an `Idempotency-Key`.

    Args:
        session: The request's tenant-scoped session.
        clock: Time source for record creation and expiry.
        key: The header value, or None when the client sent no key.
        request_fingerprint: Output of `fingerprint_request`.
        ttl: How long a key is remembered.

    Raises:
        InvalidIdempotencyKeyError: If `key` is empty, too long or has non-printable characters.
    """

    def __init__(
        self,
        session: AsyncSession,
        clock: Clock,
        *,
        key: str | None,
        request_fingerprint: str,
        ttl: timedelta,
    ) -> None:
        if key is not None and not _KEY_PATTERN.fullmatch(key):
            raise InvalidIdempotencyKeyError(
                "Idempotency-Key must be 1-255 printable ASCII characters without spaces."
            )
        self._session = session
        self._clock = clock
        self._key = key
        self._fingerprint = request_fingerprint
        self._ttl = ttl
        self._record_id: uuid.UUID | None = None

    async def claim(self) -> JSONResponse | None:
        """Claim the key for this request, or return the stored response to replay.

        Returns:
            None when the endpoint should proceed (no key, or the key was claimed now);
            otherwise the response recorded by the first request with this key.

        Raises:
            IdempotencyKeyReusedError: If the key was used for a different request.
            ConflictError: If the stored record has no response (should not happen because
                claim and completion share a transaction; defensive).
        """
        if self._key is None:
            return None
        tenant_id = require_session_tenant_id(self._session)
        now = self._clock.now()

        # Expired records are forgotten so the key can be reused.
        await self._session.execute(
            delete(IdempotencyRecord).where(
                IdempotencyRecord.key == self._key, IdempotencyRecord.expires_at <= now
            )
        )
        claimed_id = await self._session.scalar(
            insert(IdempotencyRecord)
            .values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                key=self._key,
                request_fingerprint=self._fingerprint,
                created_at=now,
                expires_at=now + self._ttl,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "key"])
            .returning(IdempotencyRecord.id)
        )
        if claimed_id is not None:
            self._record_id = claimed_id
            return None

        record = await self._session.scalar(
            select(IdempotencyRecord).where(IdempotencyRecord.key == self._key)
        )
        if record is None or record.response_status is None:
            raise ConflictError("A request with this Idempotency-Key is still being processed.")
        if record.request_fingerprint != self._fingerprint:
            raise IdempotencyKeyReusedError(
                "This Idempotency-Key was already used with a different request."
            )
        return JSONResponse(
            status_code=record.response_status,
            content=record.response_body,
            headers={REPLAYED_HEADER: "true"},
        )

    async def complete(
        self, status_code: int, response_body: BaseModel | Any, *, replay_body: Any = None
    ) -> None:
        """Store the response so later requests with the same key replay it.

        Args:
            status_code: HTTP status of the successful response.
            response_body: The response returned to this caller.
            replay_body: What to return on replay instead of `response_body`. Endpoints whose
                response contains a one-time secret pass a redacted copy here so the secret is
                never persisted.
        """
        if self._record_id is None:
            return
        stored = response_body if replay_body is None else replay_body
        record = await self._session.get(IdempotencyRecord, self._record_id)
        if record is None:  # pragma: no cover - row was inserted in this transaction
            return
        record.response_status = status_code
        record.response_body = jsonable_encoder(stored)
        await self._session.flush()


async def run_idempotent[ResponseT](
    guard: IdempotencyGuard,
    status_code: int,
    produce: Callable[[], Awaitable[ResponseT]],
) -> ResponseT | JSONResponse:
    """Run a side-effecting endpoint body at most once per `Idempotency-Key`.

    Args:
        guard: The request's idempotency guard.
        status_code: Status of a successful response.
        produce: Performs the side effect and returns the response model.

    Returns:
        The new response, or the stored response when the key was already used.
    """
    if (replay := await guard.claim()) is not None:
        return replay
    response = await produce()
    await guard.complete(status_code, response)
    return response
