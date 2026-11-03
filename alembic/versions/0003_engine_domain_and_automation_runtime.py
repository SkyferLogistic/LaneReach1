"""Engine: domain entities, events, enrollments, guardrail state, outbox, notifications, leads.

Tables were generated from the models; RLS, server defaults for new NOT NULL tenant columns and
the dispatcher lookup functions were added by hand.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from lanereach.core.tenancy import tenant_rls_statements

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_TABLES = (
    "companies",
    "country_calendars",
    "events",
    "notifications",
    "offices",
    "outbox",
    "templates",
    "company_branches",
    "company_office_starts",
    "scheduled_trigger_runs",
    "senders",
    "workflow_company_pauses",
    "contacts",
    "mailbox_daily_usage",
    "enrollments",
    "leads",
    "messages",
    "suppressions",
    "enrollment_steps",
)


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("domain", sa.String(length=253), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.Column("industry", sa.String(length=100), nullable=True),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("frozen_reason", sa.String(length=200), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_companies_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_companies")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_companies_tenant_id_id")),
    )
    op.create_index(op.f("ix_companies_tenant_id"), "companies", ["tenant_id"], unique=False)
    op.create_index(
        "ix_companies_tenant_id_domain", "companies", ["tenant_id", "domain"], unique=False
    )
    op.create_table(
        "country_calendars",
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("weekend_days", postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column("extra_holidays", postgresql.ARRAY(sa.Date()), nullable=False),
        sa.Column("extra_workdays", postgresql.ARRAY(sa.Date()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_country_calendars_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_country_calendars")),
        sa.UniqueConstraint(
            "tenant_id", "country", name=op.f("uq_country_calendars_tenant_id_country")
        ),
    )
    op.create_index(
        op.f("ix_country_calendars_tenant_id"), "country_calendars", ["tenant_id"], unique=False
    )
    op.create_table(
        "events",
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("contact_id", sa.UUID(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enrollments_created", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_events_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name=op.f("uq_events_tenant_id_idempotency_key")
        ),
    )
    op.create_index(
        "ix_events_pending",
        "events",
        ["tenant_id", "occurred_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(op.f("ix_events_tenant_id"), "events", ["tenant_id"], unique=False)
    op.create_table(
        "notifications",
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("recipient", sa.String(length=320), nullable=False),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("contact_id", sa.UUID(), nullable=True),
        sa.Column("enrollment_id", sa.UUID(), nullable=True),
        sa.Column("delivered_via", sa.String(length=50), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_notifications_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
    )
    op.create_index(
        op.f("ix_notifications_tenant_id"), "notifications", ["tenant_id"], unique=False
    )
    op.create_table(
        "offices",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("default_language", sa.String(length=2), nullable=False),
        sa.Column("owner_name", sa.String(length=200), nullable=False),
        sa.Column("owner_email", sa.String(length=320), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("sending_paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_offices_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_offices")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_offices_tenant_id_id")),
        sa.UniqueConstraint("tenant_id", "name", name=op.f("uq_offices_tenant_id_name")),
    )
    op.create_index(op.f("ix_offices_tenant_id"), "offices", ["tenant_id"], unique=False)
    op.create_table(
        "outbox",
        sa.Column("idempotency_key", sa.String(length=150), nullable=False),
        sa.Column("provider_message_id", sa.String(length=300), nullable=False),
        sa.Column("thread_id", sa.String(length=300), nullable=False),
        sa.Column("in_reply_to", sa.String(length=300), nullable=True),
        sa.Column("references", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("to_email", sa.String(length=320), nullable=False),
        sa.Column("to_name", sa.String(length=200), nullable=True),
        sa.Column("from_email", sa.String(length=320), nullable=False),
        sa.Column("from_name", sa.String(length=200), nullable=False),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("headers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("contact_id", sa.UUID(), nullable=True),
        sa.Column("office_id", sa.UUID(), nullable=True),
        sa.Column("sender_id", sa.UUID(), nullable=True),
        sa.Column("enrollment_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_outbox_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox")),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name=op.f("uq_outbox_tenant_id_idempotency_key")
        ),
    )
    op.create_index(op.f("ix_outbox_tenant_id"), "outbox", ["tenant_id"], unique=False)
    op.create_table(
        "templates",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("language", sa.String(length=2), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_templates_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_templates")),
        sa.UniqueConstraint(
            "tenant_id", "key", "language", name=op.f("uq_templates_tenant_id_key_language")
        ),
    )
    op.create_index(op.f("ix_templates_tenant_id"), "templates", ["tenant_id"], unique=False)
    op.create_table(
        "company_branches",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_tenant_scoped_company_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_company_branches_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company_branches")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_company_branches_tenant_id_id")),
    )
    op.create_index(
        op.f("ix_company_branches_tenant_id"), "company_branches", ["tenant_id"], unique=False
    )
    op.create_table(
        "company_office_starts",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("office_id", sa.UUID(), nullable=False),
        sa.Column("iso_year", sa.Integer(), nullable=False),
        sa.Column("iso_week", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_tenant_scoped_company_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "office_id"],
            ["offices.tenant_id", "offices.id"],
            name="fk_tenant_scoped_office_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_company_office_starts_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company_office_starts")),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "iso_year",
            "iso_week",
            name=op.f("uq_company_office_starts_tenant_id_company_id_iso_year_iso_week"),
        ),
    )
    op.create_index(
        op.f("ix_company_office_starts_tenant_id"),
        "company_office_starts",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "scheduled_trigger_runs",
        sa.Column("workflow_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("enrolled_count", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workflow_id"],
            ["workflows.tenant_id", "workflows.id"],
            name="fk_tenant_scoped_workflow_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_scheduled_trigger_runs_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scheduled_trigger_runs")),
        sa.UniqueConstraint(
            "tenant_id",
            "workflow_id",
            "version",
            name=op.f("uq_scheduled_trigger_runs_tenant_id_workflow_id_version"),
        ),
    )
    op.create_index(
        op.f("ix_scheduled_trigger_runs_tenant_id"),
        "scheduled_trigger_runs",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "senders",
        sa.Column("office_id", sa.UUID(), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("daily_cap", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("flagged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("flag_reason", sa.String(length=200), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "office_id"],
            ["offices.tenant_id", "offices.id"],
            name="fk_tenant_scoped_office_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_senders_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_senders")),
        sa.UniqueConstraint("tenant_id", "email", name=op.f("uq_senders_tenant_id_email")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_senders_tenant_id_id")),
    )
    op.create_index(op.f("ix_senders_tenant_id"), "senders", ["tenant_id"], unique=False)
    op.create_table(
        "workflow_company_pauses",
        sa.Column("workflow_id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_tenant_scoped_company_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workflow_id"],
            ["workflows.tenant_id", "workflows.id"],
            name="fk_tenant_scoped_workflow_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_workflow_company_pauses_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_company_pauses")),
        sa.UniqueConstraint(
            "tenant_id",
            "workflow_id",
            "company_id",
            name=op.f("uq_workflow_company_pauses_tenant_id_workflow_id_company_id"),
        ),
    )
    op.create_index(
        op.f("ix_workflow_company_pauses_tenant_id"),
        "workflow_company_pauses",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "contacts",
        sa.Column("company_id", sa.UUID(), nullable=True),
        sa.Column("branch_id", sa.UUID(), nullable=True),
        sa.Column("office_id", sa.UUID(), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("first_name", sa.String(length=100), nullable=True),
        sa.Column("last_name", sa.String(length=100), nullable=True),
        sa.Column("job_title", sa.String(length=200), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("language", sa.String(length=2), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column("stage", sa.String(length=30), nullable=False),
        sa.Column("owner_email", sa.String(length=320), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "branch_id"],
            ["company_branches.tenant_id", "company_branches.id"],
            name="fk_tenant_scoped_branch_id",
            ondelete="SET NULL (branch_id)",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_tenant_scoped_company_id",
            ondelete="SET NULL (company_id)",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "office_id"],
            ["offices.tenant_id", "offices.id"],
            name="fk_tenant_scoped_office_id",
            ondelete="SET NULL (office_id)",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_contacts_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contacts")),
        sa.UniqueConstraint("tenant_id", "email", name=op.f("uq_contacts_tenant_id_email")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_contacts_tenant_id_id")),
    )
    op.create_index(op.f("ix_contacts_tenant_id"), "contacts", ["tenant_id"], unique=False)
    op.create_table(
        "mailbox_daily_usage",
        sa.Column("sender_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("sent_count", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "sender_id"],
            ["senders.tenant_id", "senders.id"],
            name="fk_tenant_scoped_sender_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_mailbox_daily_usage_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mailbox_daily_usage")),
        sa.UniqueConstraint(
            "tenant_id",
            "sender_id",
            "day",
            name=op.f("uq_mailbox_daily_usage_tenant_id_sender_id_day"),
        ),
    )
    op.create_index(
        op.f("ix_mailbox_daily_usage_tenant_id"), "mailbox_daily_usage", ["tenant_id"], unique=False
    )
    op.create_table(
        "enrollments",
        sa.Column("workflow_id", sa.UUID(), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("contact_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("current_node_id", sa.String(length=64), nullable=False),
        sa.Column("resume_handle", sa.String(length=64), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("variables", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("stop_reason", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("step_count", sa.Integer(), nullable=False),
        sa.Column("trigger_event_id", sa.UUID(), nullable=True),
        sa.Column("enrolled_by", sa.String(length=200), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            ["contacts.tenant_id", "contacts.id"],
            name="fk_tenant_scoped_contact_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workflow_id"],
            ["workflows.tenant_id", "workflows.id"],
            name="fk_tenant_scoped_workflow_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_enrollments_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_enrollments")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_enrollments_tenant_id_id")),
    )
    op.create_index(
        "ix_enrollments_due", "enrollments", ["tenant_id", "status", "next_run_at"], unique=False
    )
    op.create_index(op.f("ix_enrollments_tenant_id"), "enrollments", ["tenant_id"], unique=False)
    op.create_index(
        "ix_enrollments_tenant_id_contact_id",
        "enrollments",
        ["tenant_id", "contact_id"],
        unique=False,
    )
    op.create_index(
        "uq_enrollments_one_open_per_contact",
        "enrollments",
        ["tenant_id", "workflow_id", "contact_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('active', 'waiting', 'paused')"),
    )
    op.create_table(
        "leads",
        sa.Column("contact_id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=True),
        sa.Column("office_id", sa.UUID(), nullable=True),
        sa.Column("message_id", sa.UUID(), nullable=True),
        sa.Column("enrollment_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("priority", sa.String(length=10), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("shipment", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            ["contacts.tenant_id", "contacts.id"],
            name="fk_tenant_scoped_contact_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_leads_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_leads")),
    )
    op.create_index(op.f("ix_leads_tenant_id"), "leads", ["tenant_id"], unique=False)
    op.create_table(
        "messages",
        sa.Column("contact_id", sa.UUID(), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("message_id", sa.String(length=300), nullable=False),
        sa.Column("thread_id", sa.String(length=300), nullable=False),
        sa.Column("in_reply_to", sa.String(length=300), nullable=True),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("from_email", sa.String(length=320), nullable=False),
        sa.Column("to_email", sa.String(length=320), nullable=False),
        sa.Column("office_id", sa.UUID(), nullable=True),
        sa.Column("sender_id", sa.UUID(), nullable=True),
        sa.Column("enrollment_id", sa.UUID(), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("body_purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            ["contacts.tenant_id", "contacts.id"],
            name="fk_tenant_scoped_contact_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_messages_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_messages_tenant_id_id")),
        sa.UniqueConstraint(
            "tenant_id", "message_id", name=op.f("uq_messages_tenant_id_message_id")
        ),
    )
    op.create_index(op.f("ix_messages_tenant_id"), "messages", ["tenant_id"], unique=False)
    op.create_index(
        "ix_messages_tenant_id_contact_id_occurred_at",
        "messages",
        ["tenant_id", "contact_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_messages_tenant_id_thread_id", "messages", ["tenant_id", "thread_id"], unique=False
    )
    op.create_table(
        "suppressions",
        sa.Column("contact_id", sa.UUID(), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("reason", sa.String(length=30), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            ["contacts.tenant_id", "contacts.id"],
            name="fk_tenant_scoped_contact_id",
            ondelete="SET NULL (contact_id)",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_suppressions_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_suppressions")),
    )
    op.create_index(op.f("ix_suppressions_tenant_id"), "suppressions", ["tenant_id"], unique=False)
    op.create_index(
        "ix_suppressions_tenant_id_contact_id",
        "suppressions",
        ["tenant_id", "contact_id"],
        unique=False,
    )
    op.create_index(
        "ix_suppressions_tenant_id_email", "suppressions", ["tenant_id", "email"], unique=False
    )
    op.create_table(
        "enrollment_steps",
        sa.Column("enrollment_id", sa.UUID(), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("node_type", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", sa.String(length=20), nullable=False),
        sa.Column("handle_taken", sa.String(length=64), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("wait_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("guardrail_decisions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "enrollment_id"],
            ["enrollments.tenant_id", "enrollments.id"],
            name="fk_tenant_scoped_enrollment_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_enrollment_steps_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_enrollment_steps")),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name=op.f("uq_enrollment_steps_tenant_id_idempotency_key"),
        ),
    )
    op.create_index(
        "ix_enrollment_steps_enrollment",
        "enrollment_steps",
        ["tenant_id", "enrollment_id", "step_index"],
        unique=False,
    )
    op.create_index(
        op.f("ix_enrollment_steps_tenant_id"), "enrollment_steps", ["tenant_id"], unique=False
    )
    op.add_column(
        "tenants", sa.Column("sending_paused_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "tenants",
        sa.Column("sending_window_start", sa.Time(), nullable=False, server_default="09:00"),
    )
    op.add_column(
        "tenants",
        sa.Column("sending_window_end", sa.Time(), nullable=False, server_default="11:30"),
    )

    for table in NEW_TABLES:
        for statement in tenant_rls_statements(table):
            op.execute(statement)

    # The dispatcher runs without a tenant; it may only learn *which* tenants have work.
    op.execute(
        """
        CREATE FUNCTION automation_tenants_with_work(p_now timestamptz)
        RETURNS TABLE (tenant_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
        AS $$
            SELECT t.id FROM public.tenants AS t
            WHERE t.status = 'active' AND (
                EXISTS (SELECT 1 FROM public.events AS e
                        WHERE e.tenant_id = t.id AND e.status = 'pending')
                OR EXISTS (SELECT 1 FROM public.enrollments AS n
                           WHERE n.tenant_id = t.id AND n.status IN ('active', 'waiting')
                             AND n.next_run_at <= p_now)
                OR EXISTS (SELECT 1 FROM public.workflows AS w
                           JOIN public.workflow_versions AS v
                             ON v.workflow_id = w.id AND v.version = w.current_version
                           WHERE w.tenant_id = t.id AND w.status = 'published'
                             AND w.paused_at IS NULL
                             AND v.graph -> 'trigger' ->> 'type' = 'trigger_scheduled_date'
                             AND NOT EXISTS (SELECT 1 FROM public.scheduled_trigger_runs AS r
                                             WHERE r.workflow_id = w.id
                                               AND r.version = w.current_version))
            )
        $$
        """
    )
    # Dev-mode login screen only (the endpoint is not mounted unless AUTH_MODE=dev).
    op.execute(
        """
        CREATE FUNCTION auth_list_active_tenants()
        RETURNS TABLE (tenant_id uuid, name text, slug text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
        AS $$
            SELECT id, name, slug FROM public.tenants WHERE status = 'active' ORDER BY name
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth_list_active_tenants()")
    op.execute("DROP FUNCTION IF EXISTS automation_tenants_with_work(timestamptz)")
    op.drop_column("tenants", "sending_window_end")
    op.drop_column("tenants", "sending_window_start")
    op.drop_column("tenants", "sending_paused_at")
    op.drop_index(op.f("ix_enrollment_steps_tenant_id"), table_name="enrollment_steps")
    op.drop_index("ix_enrollment_steps_enrollment", table_name="enrollment_steps")
    op.drop_table("enrollment_steps")
    op.drop_index("ix_suppressions_tenant_id_email", table_name="suppressions")
    op.drop_index("ix_suppressions_tenant_id_contact_id", table_name="suppressions")
    op.drop_index(op.f("ix_suppressions_tenant_id"), table_name="suppressions")
    op.drop_table("suppressions")
    op.drop_index("ix_messages_tenant_id_thread_id", table_name="messages")
    op.drop_index("ix_messages_tenant_id_contact_id_occurred_at", table_name="messages")
    op.drop_index(op.f("ix_messages_tenant_id"), table_name="messages")
    op.drop_table("messages")
    op.drop_index(op.f("ix_leads_tenant_id"), table_name="leads")
    op.drop_table("leads")
    op.drop_index(
        "uq_enrollments_one_open_per_contact",
        table_name="enrollments",
        postgresql_where=sa.text("status IN ('active', 'waiting', 'paused')"),
    )
    op.drop_index("ix_enrollments_tenant_id_contact_id", table_name="enrollments")
    op.drop_index(op.f("ix_enrollments_tenant_id"), table_name="enrollments")
    op.drop_index("ix_enrollments_due", table_name="enrollments")
    op.drop_table("enrollments")
    op.drop_index(op.f("ix_mailbox_daily_usage_tenant_id"), table_name="mailbox_daily_usage")
    op.drop_table("mailbox_daily_usage")
    op.drop_index(op.f("ix_contacts_tenant_id"), table_name="contacts")
    op.drop_table("contacts")
    op.drop_index(
        op.f("ix_workflow_company_pauses_tenant_id"), table_name="workflow_company_pauses"
    )
    op.drop_table("workflow_company_pauses")
    op.drop_index(op.f("ix_senders_tenant_id"), table_name="senders")
    op.drop_table("senders")
    op.drop_index(op.f("ix_scheduled_trigger_runs_tenant_id"), table_name="scheduled_trigger_runs")
    op.drop_table("scheduled_trigger_runs")
    op.drop_index(op.f("ix_company_office_starts_tenant_id"), table_name="company_office_starts")
    op.drop_table("company_office_starts")
    op.drop_index(op.f("ix_company_branches_tenant_id"), table_name="company_branches")
    op.drop_table("company_branches")
    op.drop_index(op.f("ix_templates_tenant_id"), table_name="templates")
    op.drop_table("templates")
    op.drop_index(op.f("ix_outbox_tenant_id"), table_name="outbox")
    op.drop_table("outbox")
    op.drop_index(op.f("ix_offices_tenant_id"), table_name="offices")
    op.drop_table("offices")
    op.drop_index(op.f("ix_notifications_tenant_id"), table_name="notifications")
    op.drop_table("notifications")
    op.drop_index(op.f("ix_events_tenant_id"), table_name="events")
    op.drop_index(
        "ix_events_pending", table_name="events", postgresql_where=sa.text("status = 'pending'")
    )
    op.drop_table("events")
    op.drop_index(op.f("ix_country_calendars_tenant_id"), table_name="country_calendars")
    op.drop_table("country_calendars")
    op.drop_index("ix_companies_tenant_id_domain", table_name="companies")
    op.drop_index(op.f("ix_companies_tenant_id"), table_name="companies")
    op.drop_table("companies")
