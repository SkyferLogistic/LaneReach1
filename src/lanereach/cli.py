"""Operational command line: `lanereach-admin bootstrap-db | migrate | purge-expired-data | seed`.

`bootstrap-db` needs superuser credentials (`DATABASE_ADMIN_URL`); `migrate` runs Alembic as the
owner role. Docker Compose runs both in the one-shot `migrate` service.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from lanereach.core.config import get_settings
from lanereach.core.db_bootstrap import bootstrap_database_roles, plan_database_roles


def build_alembic_config(database_url: str, config_path: Path | None = None) -> Config:
    """Create an Alembic config that migrates `database_url`.

    Args:
        database_url: Owner-role SQLAlchemy URL.
        config_path: Path to `alembic.ini`; defaults to the one in the working directory.

    Returns:
        The Alembic config.
    """
    config = Config(str(config_path or Path("alembic.ini")))
    config.attributes["database_url"] = database_url
    return config


def bootstrap_database() -> None:
    """Create the owner and app roles using `DATABASE_ADMIN_URL`.

    Raises:
        SystemExit: If `DATABASE_ADMIN_URL` is not set.
    """
    admin_url = os.environ.get("DATABASE_ADMIN_URL")
    if not admin_url:
        raise SystemExit("DATABASE_ADMIN_URL is required for bootstrap-db")
    settings = get_settings()
    plan = plan_database_roles(
        owner_url=settings.database_owner_url.get_secret_value(),
        app_url=settings.database_url.get_secret_value(),
    )
    dsn = make_url(admin_url).set(drivername="postgresql").render_as_string(hide_password=False)
    asyncio.run(bootstrap_database_roles(dsn, plan))
    print(f"roles ready: owner={plan.owner.name} app={plan.app.name}")  # noqa: T201


def migrate_database() -> None:
    """Upgrade the schema to the latest Alembic revision as the owner role."""
    settings = get_settings()
    command.upgrade(build_alembic_config(settings.database_owner_url.get_secret_value()), "head")


def main(argv: list[str] | None = None) -> int:
    """Entry point of `lanereach-admin`.

    Args:
        argv: Arguments (defaults to `sys.argv[1:]`).

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(prog="lanereach-admin")
    parser.add_argument(
        "command", choices=["bootstrap-db", "migrate", "purge-expired-data", "seed"]
    )
    arguments = parser.parse_args(argv)
    if arguments.command == "bootstrap-db":
        bootstrap_database()
    elif arguments.command == "migrate":
        migrate_database()
    elif arguments.command == "purge-expired-data":
        asyncio.run(purge_expired_data())
    else:
        asyncio.run(seed_demo_data())
    return 0


async def purge_expired_data() -> None:
    """Run the retention purge once now (it also runs daily on Celery beat)."""
    from lanereach.core.container import build_service_container
    from lanereach.core.model_registry import load_all_models
    from lanereach.maintenance.retention import purge_all_tenants

    load_all_models()
    container = build_service_container(get_settings())
    try:
        run = await purge_all_tenants(container)
    finally:
        await container.aclose()
    print(f"purged in {run.tenants} tenant(s): {run.purged}; errors: {run.errors or 'none'}")  # noqa: T201


async def seed_demo_data() -> None:
    """Load the demo tenants (skips tenants that already exist)."""
    from lanereach.core.clock import SystemClock
    from lanereach.core.model_registry import load_all_models
    from lanereach.seed.loader import seed_from_settings

    load_all_models()
    for tenant in await seed_from_settings(get_settings(), SystemClock()):
        if tenant.created:
            state = "created"
        elif tenant.counts:
            state = "already exists; added what was missing:"
        else:
            state = "already exists, left unchanged"
        print(f"{tenant.name} ({tenant.slug}, {tenant.tenant_id}): {state} {tenant.counts or ''}")  # noqa: T201
    print("Log in: POST /api/v1/auth/dev-token with one of the tenant IDs above (dev mode).")  # noqa: T201


if __name__ == "__main__":
    sys.exit(main())
