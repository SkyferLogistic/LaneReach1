"""Talking to mail servers: SMTP to send, IMAP to read new messages.

Uses the standard library (`smtplib`, `imaplib`) in worker threads so the event loop never
blocks. Every connection is verified TLS unless the mailbox is explicitly set to `none`, which is
only allowed in development against a local test server. Passwords are passed in memory only and
never appear in errors or logs; server replies are shortened before they are stored.
"""

from __future__ import annotations

import asyncio
import contextlib
import imaplib
import smtplib
import ssl
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from lanereach.integrations.mailboxes.models import MailSecurity


class MailServerError(Exception):
    """A mail server refused or failed.

    Args:
        message: What happened, safe to show (never contains the password).
        permanent: True when retrying cannot help (bad login, rejected recipient).
    """

    def __init__(self, message: str, *, permanent: bool) -> None:
        super().__init__(message)
        self.permanent = permanent


@dataclass(frozen=True)
class MailServer:
    """Where and how to connect."""

    host: str
    port: int
    security: MailSecurity
    username: str
    password: str = field(repr=False)
    timeout: float = 20.0


@dataclass(frozen=True)
class FetchedMessages:
    """New messages of a mailbox since the last poll."""

    uid_validity: int
    max_uid: int
    messages: list[tuple[int, bytes]]
    baseline: bool = False  # first poll: remember where the inbox is, read nothing


class MailGateway(Protocol):
    """Sends and reads email for agent mailboxes (a fake in tests)."""

    async def send(
        self, server: MailServer, sender: str, recipients: Sequence[str], data: bytes
    ) -> None:
        """Deliver one email."""
        ...

    async def check_smtp(self, server: MailServer) -> None:
        """Log in to SMTP without sending."""
        ...

    async def check_imap(self, server: MailServer) -> None:
        """Log in to IMAP and open the inbox without reading."""
        ...

    async def fetch_new(
        self, server: MailServer, uid_validity: int | None, last_seen_uid: int | None, limit: int
    ) -> FetchedMessages:
        """Messages newer than `last_seen_uid` (a baseline when the inbox is new to us)."""
        ...


def _short(value: object) -> str:
    text = value.decode(errors="replace") if isinstance(value, bytes) else str(value)
    return " ".join(text.split())[:200]


class StdlibMailGateway:
    """`MailGateway` over `smtplib` and `imaplib`."""

    async def send(
        self, server: MailServer, sender: str, recipients: Sequence[str], data: bytes
    ) -> None:
        """Deliver one email over SMTP.

        Raises:
            MailServerError: When the server refuses (permanent) or is unreachable (temporary).
        """
        await asyncio.to_thread(self._send, server, sender, list(recipients), data)

    async def check_smtp(self, server: MailServer) -> None:
        """Log in to SMTP (nothing is sent).

        Raises:
            MailServerError: If the connection or the login fails.
        """
        await asyncio.to_thread(self._smtp_login_only, server)

    async def check_imap(self, server: MailServer) -> None:
        """Log in to IMAP and select the inbox read-only.

        Raises:
            MailServerError: If the connection or the login fails.
        """
        await asyncio.to_thread(self._imap_check, server)

    async def fetch_new(
        self, server: MailServer, uid_validity: int | None, last_seen_uid: int | None, limit: int
    ) -> FetchedMessages:
        """Read messages newer than `last_seen_uid` without marking them read.

        Raises:
            MailServerError: If the connection or the login fails.
        """
        return await asyncio.to_thread(self._fetch, server, uid_validity, last_seen_uid, limit)

    # --- SMTP ------------------------------------------------------------------------------

    def _smtp(self, server: MailServer) -> smtplib.SMTP:
        context = ssl.create_default_context()
        try:
            if server.security is MailSecurity.SSL:
                client: smtplib.SMTP = smtplib.SMTP_SSL(
                    server.host, server.port, timeout=server.timeout, context=context
                )
            else:
                client = smtplib.SMTP(server.host, server.port, timeout=server.timeout)
                client.ehlo()
                if server.security is MailSecurity.STARTTLS:
                    client.starttls(context=context)
                    client.ehlo()
            if server.password and (
                server.security is not MailSecurity.NONE or client.has_extn("auth")
            ):
                client.login(server.username, server.password)
        except smtplib.SMTPAuthenticationError as exc:
            raise MailServerError(
                f"The SMTP server refused the login ({exc.smtp_code} {_short(exc.smtp_error)}).",
                permanent=True,
            ) from None
        except (smtplib.SMTPException, OSError) as exc:
            raise MailServerError(
                f"Cannot connect to {server.host}:{server.port} over SMTP ({_short(exc)}).",
                permanent=False,
            ) from None
        return client

    def _smtp_login_only(self, server: MailServer) -> None:
        client = self._smtp(server)
        try:
            client.quit()
        except (smtplib.SMTPException, OSError):
            client.close()

    def _send(self, server: MailServer, sender: str, recipients: list[str], data: bytes) -> None:
        client = self._smtp(server)
        try:
            refused = client.sendmail(sender, recipients, data)
            if refused:
                raise MailServerError(
                    f"Some recipients were refused: {_short(refused)}.", permanent=True
                )
        except smtplib.SMTPRecipientsRefused as exc:
            raise MailServerError(
                f"The recipient was refused ({_short(exc.recipients)}).", permanent=True
            ) from None
        except smtplib.SMTPResponseException as exc:
            raise MailServerError(
                f"The SMTP server answered {exc.smtp_code} {_short(exc.smtp_error)}.",
                permanent=exc.smtp_code >= 500,
            ) from None
        except (smtplib.SMTPException, OSError) as exc:
            raise MailServerError(f"Sending failed ({_short(exc)}).", permanent=False) from None
        finally:
            try:
                client.quit()
            except (smtplib.SMTPException, OSError):
                client.close()

    # --- IMAP ------------------------------------------------------------------------------

    def _imap(self, server: MailServer) -> imaplib.IMAP4:
        context = ssl.create_default_context()
        try:
            if server.security is MailSecurity.SSL:
                client: imaplib.IMAP4 = imaplib.IMAP4_SSL(
                    server.host, server.port, ssl_context=context, timeout=server.timeout
                )
            else:
                client = imaplib.IMAP4(server.host, server.port, timeout=server.timeout)
                if server.security is MailSecurity.STARTTLS:
                    client.starttls(ssl_context=context)
            client.login(server.username, server.password)
        except imaplib.IMAP4.error as exc:
            raise MailServerError(
                f"The IMAP server refused the login ({_short(exc)}).", permanent=True
            ) from None
        except OSError as exc:
            raise MailServerError(
                f"Cannot connect to {server.host}:{server.port} over IMAP ({_short(exc)}).",
                permanent=False,
            ) from None
        return client

    def _select_inbox(self, client: imaplib.IMAP4) -> int:
        status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            raise MailServerError("The inbox cannot be opened.", permanent=False)
        _, values = client.response("UIDVALIDITY")
        raw = values[0] if values and values[0] else b"0"
        return int(raw.decode() if isinstance(raw, bytes) else raw)

    def _imap_check(self, server: MailServer) -> None:
        client = self._imap(server)
        try:
            self._select_inbox(client)
        finally:
            _logout(client)

    def _fetch(
        self, server: MailServer, uid_validity: int | None, last_seen_uid: int | None, limit: int
    ) -> FetchedMessages:
        client = self._imap(server)
        try:
            validity = self._select_inbox(client)
            status, data = client.uid("SEARCH", "ALL")
            if status != "OK":
                raise MailServerError("The inbox cannot be searched.", permanent=False)
            uids = sorted(int(item) for item in (data[0] or b"").split())
            max_uid = uids[-1] if uids else 0
            if last_seen_uid is None or uid_validity != validity:
                # New to us (or the server renumbered the inbox): start from now, read nothing old.
                return FetchedMessages(validity, max_uid, [], baseline=True)
            new = [uid for uid in uids if uid > last_seen_uid][:limit]
            messages: list[tuple[int, bytes]] = []
            for uid in new:
                status, parts = client.uid("FETCH", str(uid), "(BODY.PEEK[])")
                if status != "OK":
                    continue
                for part in parts:
                    if isinstance(part, tuple) and len(part) == 2 and isinstance(part[1], bytes):
                        messages.append((uid, part[1]))
                        break
            return FetchedMessages(validity, new[-1] if new else last_seen_uid, messages)
        except imaplib.IMAP4.error as exc:
            raise MailServerError(
                f"Reading the inbox failed ({_short(exc)}).", permanent=False
            ) from None
        except OSError as exc:
            raise MailServerError(
                f"The IMAP connection dropped ({_short(exc)}).", permanent=False
            ) from None
        finally:
            _logout(client)


def _logout(client: imaplib.IMAP4) -> None:
    with contextlib.suppress(imaplib.IMAP4.error, OSError):
        client.logout()
