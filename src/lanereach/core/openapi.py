"""Helpers for documenting endpoints in OpenAPI."""

from __future__ import annotations

from typing import Any

from lanereach.core.errors import ProblemDocument

_DESCRIPTIONS = {
    400: "Malformed request",
    401: "Missing or invalid credentials",
    403: "Missing scope",
    404: "Not found (or owned by another tenant)",
    409: "Conflict with the current state",
    413: "Request body too large",
    422: "Validation failed or business rule violated",
    429: "Rate limited",
}


def problem_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """Document RFC 7807 error responses.

    Args:
        *status_codes: HTTP status codes the endpoint may return.

    Returns:
        A `responses=` mapping for FastAPI route decorators.
    """
    return {
        code: {"model": ProblemDocument, "description": _DESCRIPTIONS.get(code, "Error")}
        for code in status_codes
    }


READ = problem_responses(401, 403, 404)
WRITE = problem_responses(401, 403, 404, 409, 422)
