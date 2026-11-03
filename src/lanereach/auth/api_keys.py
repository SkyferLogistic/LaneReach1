"""Tenant API key format, generation and hashing.

Format: `lr_live_<random>` or `lr_test_<random>` where `<random>` is 43 URL-safe characters
(256 bits). The first 16 characters (`lr_live_` + 8 random characters) are the lookup prefix
stored in clear; the full key is stored only as a SHA-256 hex digest. A fast hash is
appropriate here (unlike passwords) because the key has 256 bits of entropy.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

from lanereach.auth.models import ApiKeyEnvironment

API_KEY_PREFIX_LENGTH = 16
_RANDOM_BYTES = 32
_API_KEY_PATTERN = re.compile(r"^lr_(live|test)_[A-Za-z0-9_-]{43}$")


@dataclass(frozen=True, slots=True)
class GeneratedApiKey:
    """A new API key. `plaintext` is returned to the caller once and never stored."""

    plaintext: str
    prefix: str
    key_hash: str
    environment: ApiKeyEnvironment


def generate_api_key(environment: ApiKeyEnvironment) -> GeneratedApiKey:
    """Create a new random API key.

    Args:
        environment: Key family, reflected in the `lr_live_` / `lr_test_` prefix.

    Returns:
        The key, its lookup prefix and its hash.
    """
    plaintext = f"lr_{environment.value}_{secrets.token_urlsafe(_RANDOM_BYTES)}"
    return GeneratedApiKey(
        plaintext=plaintext,
        prefix=plaintext[:API_KEY_PREFIX_LENGTH],
        key_hash=hash_api_key(plaintext),
        environment=environment,
    )


def hash_api_key(plaintext: str) -> str:
    """Return the SHA-256 hex digest stored for an API key.

    Args:
        plaintext: The full API key.

    Returns:
        64 lowercase hex characters.
    """
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def looks_like_api_key(token: str) -> bool:
    """Return whether a bearer token has the API key shape (rather than a JWT).

    Args:
        token: The bearer credential.

    Returns:
        True for well-formed `lr_live_…` / `lr_test_…` strings.
    """
    return _API_KEY_PATTERN.fullmatch(token) is not None


def api_key_prefix(plaintext: str) -> str:
    """Return the lookup prefix of an API key.

    Args:
        plaintext: The full API key.

    Returns:
        The first `API_KEY_PREFIX_LENGTH` characters.
    """
    return plaintext[:API_KEY_PREFIX_LENGTH]


def api_key_matches_hash(plaintext: str, stored_hash: str) -> bool:
    """Compare a presented key with a stored hash in constant time.

    Args:
        plaintext: The key presented by the caller.
        stored_hash: The hash stored for the candidate key row.

    Returns:
        True when the key matches.
    """
    return hmac.compare_digest(hash_api_key(plaintext), stored_hash)
