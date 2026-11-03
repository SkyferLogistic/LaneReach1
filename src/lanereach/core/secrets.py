"""Encryption of integration secrets stored in the database (webhook URLs, signing keys, API keys).

Secrets are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) under `INTEGRATIONS_ENCRYPTION_KEY`.
Local and test environments without a key derive one from the unsubscribe secret so they work
out of the box; staging and production refuse to start without a real key (see `Settings`).
The API never returns a secret after it was saved, only a hint such as `hooks.zapier.com/…`.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from lanereach.core.config import Settings


class SecretDecryptionError(Exception):
    """A stored secret cannot be decrypted (wrong or rotated key)."""


class SecretBox:
    """Encrypts and decrypts short secrets.

    Args:
        key: A Fernet key (32 url-safe base64-encoded bytes).
    """

    def __init__(self, key: str | bytes) -> None:
        self._fernet = Fernet(key)

    def encrypt(self, value: str) -> str:
        """Encrypt a secret.

        Args:
            value: The plain secret.

        Returns:
            The token to store.
        """
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, token: str) -> str:
        """Decrypt a stored secret.

        Args:
            token: The stored token.

        Returns:
            The plain secret.

        Raises:
            SecretDecryptionError: If the token was made with another key or is damaged.
        """
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise SecretDecryptionError("the secret cannot be decrypted with this key") from exc


def build_secret_box(settings: Settings) -> SecretBox:
    """The secret box for the process.

    Args:
        settings: Configuration (`INTEGRATIONS_ENCRYPTION_KEY`).

    Returns:
        The box.
    """
    configured = settings.integrations_encryption_key
    if configured is not None:
        return SecretBox(configured.get_secret_value())
    # Local and test only (production refuses to start without a key).
    seed = settings.unsubscribe_signing_secret.get_secret_value().encode()
    derived = hashlib.sha256(b"lanereach:integrations:" + seed).digest()
    return SecretBox(base64.urlsafe_b64encode(derived))


def secret_hint(value: str, *, keep: int = 4) -> str:
    """A safe reminder of a secret: the host of a URL, or the last characters of a key.

    Args:
        value: The plain secret.
        keep: Characters of a key to show.

    Returns:
        For example `hooks.slack.com/…` or `…a1b2`.
    """
    if value.startswith(("https://", "http://")):
        host = value.split("://", 1)[1].split("/", 1)[0]
        return f"{host}/…"
    return f"…{value[-keep:]}" if len(value) > keep * 2 else "…"
