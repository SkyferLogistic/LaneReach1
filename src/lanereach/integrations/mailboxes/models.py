"""An agent's mailbox connection (SMTP to send, IMAP to read replies).

The password is stored encrypted (`lanereach.core.secrets`) and never returned by the API or
logged. One mailbox per agent: the agent's address is the From of everything they send.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from lanereach.core.database import (
    Base,
    CreatedAtMixin,
    StrEnumType,
    TenantScopedMixin,
    UUIDPrimaryKeyMixin,
    tenant_foreign_key,
    tenant_unique_id,
)


class MailSecurity(StrEnum):
    """How the connection to the mail server is protected."""

    SSL = "ssl"  # TLS from the first byte (SMTP 465, IMAP 993)
    STARTTLS = "starttls"  # plain connection upgraded to TLS (SMTP 587, IMAP 143)
    NONE = "none"  # development only (a local test mail server)


class MailboxStatus(StrEnum):
    """Whether LaneReach can use the mailbox."""

    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    ERROR = "error"


class SenderMailbox(UUIDPrimaryKeyMixin, TenantScopedMixin, CreatedAtMixin, Base):
    """SMTP and IMAP settings of one agent's mailbox."""

    __tablename__ = "sender_mailboxes"
    __table_args__ = (
        tenant_foreign_key("sender_id", "senders"),
        UniqueConstraint("tenant_id", "sender_id"),
        tenant_unique_id(),
    )

    sender_id: Mapped[uuid.UUID]
    preset: Mapped[str] = mapped_column(String(30), default="custom")
    smtp_host: Mapped[str] = mapped_column(String(255))
    smtp_port: Mapped[int] = mapped_column(Integer)
    smtp_security: Mapped[MailSecurity] = mapped_column(StrEnumType(MailSecurity, 10))
    imap_host: Mapped[str] = mapped_column(String(255))
    imap_port: Mapped[int] = mapped_column(Integer)
    imap_security: Mapped[MailSecurity] = mapped_column(StrEnumType(MailSecurity, 10))
    username: Mapped[str] = mapped_column(String(320))
    password_encrypted: Mapped[str] = mapped_column(Text)
    status: Mapped[MailboxStatus] = mapped_column(
        StrEnumType(MailboxStatus, 12), default=MailboxStatus.UNVERIFIED
    )
    last_error: Mapped[str | None] = mapped_column(String(500))
    verified_at: Mapped[datetime | None]
    last_polled_at: Mapped[datetime | None]
    # IMAP bookkeeping: new messages are those above `last_seen_uid` in this UIDVALIDITY.
    uid_validity: Mapped[int | None] = mapped_column(BigInteger)
    last_seen_uid: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime]


class MailboxPreset(StrEnum):
    """Known providers (host names and ports only; the person still enters their login)."""

    MICROSOFT_365 = "microsoft_365"
    GOOGLE_WORKSPACE = "google_workspace"
    ZOHO = "zoho"
    CUSTOM = "custom"


PRESETS: dict[MailboxPreset, dict[str, str | int]] = {
    MailboxPreset.MICROSOFT_365: {
        "label": "Microsoft 365 / Outlook",
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
        "imap_security": "ssl",
        "help": "SMTP AUTH must be enabled for the mailbox (Microsoft 365 admin centre → user "
        "→ Mail → Manage email apps). Use an app password when multi-factor sign-in is on.",
    },
    MailboxPreset.GOOGLE_WORKSPACE: {
        "label": "Google Workspace / Gmail",
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "imap_security": "ssl",
        "help": "Turn on 2-step verification and create an app password (Google account → "
        "Security → App passwords); IMAP must be enabled in Gmail settings.",
    },
    MailboxPreset.ZOHO: {
        "label": "Zoho Mail",
        "smtp_host": "smtp.zoho.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "imap_host": "imap.zoho.com",
        "imap_port": 993,
        "imap_security": "ssl",
        "help": "Enable IMAP access in Zoho Mail settings; use an app-specific password.",
    },
    MailboxPreset.CUSTOM: {
        "label": "Other (enter the servers)",
        "smtp_host": "",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "imap_host": "",
        "imap_port": 993,
        "imap_security": "ssl",
        "help": "Your email provider lists its SMTP and IMAP servers in its help pages.",
    },
}
