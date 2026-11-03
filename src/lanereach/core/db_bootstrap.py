"""One-time database role setup, run with superuser credentials before migrations.

Creates two roles, derived from `DATABASE_OWNER_URL` and `DATABASE_URL`:

* the **owner** role owns every table and runs Alembic migrations;
* the **app** role is what the API and workers connect as. It owns nothing and has no
  `BYPASSRLS`, so Row-Level Security policies always apply to it.

Default privileges make every table the owner creates readable/writable by the app role, and
every function executable only by the app role (not `PUBLIC`), so migrations never need to know
the app role's name. The script is idempotent and safe to run on every deploy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import asyncpg
from sqlalchemy.engine import make_url

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


@dataclass(frozen=True, slots=True)
class RoleCredentials:
    """A login role and its password."""

    name: str
    password: str


@dataclass(frozen=True, slots=True)
class DatabaseRolePlan:
    """What `bootstrap_database_roles` creates."""

    database: str
    owner: RoleCredentials
    app: RoleCredentials


def plan_database_roles(*, owner_url: str, app_url: str) -> DatabaseRolePlan:
    """Derive role names and passwords from the owner and app connection URLs.

    Args:
        owner_url: `DATABASE_OWNER_URL`.
        app_url: `DATABASE_URL`.

    Returns:
        The plan.

    Raises:
        ValueError: If a URL lacks a user, password or database, the two URLs target different
            databases or the same role, or a name is not a simple lowercase identifier.
    """
    owner, app = make_url(owner_url), make_url(app_url)
    for label, url in (("owner", owner), ("app", app)):
        if not url.username or not url.password or not url.database:
            raise ValueError(f"{label} database URL needs a user, password and database")
        for identifier in (url.username, url.database):
            if not _IDENTIFIER.fullmatch(identifier):
                raise ValueError(f"'{identifier}' is not a simple lowercase SQL identifier")
    if owner.database != app.database:
        raise ValueError("owner and app URLs must point at the same database")
    if owner.username == app.username:
        raise ValueError("the app role must be different from the owner role")
    return DatabaseRolePlan(
        database=str(owner.database),
        owner=RoleCredentials(str(owner.username), str(owner.password)),
        app=RoleCredentials(str(app.username), str(app.password)),
    )


async def bootstrap_database_roles(admin_dsn: str, plan: DatabaseRolePlan) -> None:
    """Create or update the owner and app roles and their privileges.

    Args:
        admin_dsn: A superuser DSN (`postgresql://...`, no SQLAlchemy driver suffix) for the
            target database.
        plan: Output of `plan_database_roles`.
    """
    connection = await asyncpg.connect(admin_dsn)
    try:
        for role in (plan.owner, plan.app):
            await _upsert_login_role(connection, role)
        owner, app, database = plan.owner.name, plan.app.name, plan.database
        statements = [
            f"ALTER ROLE {owner} NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS",
            f"ALTER ROLE {app} NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT",
            f"GRANT CONNECT ON DATABASE {database} TO {owner}, {app}",
            "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
            f"GRANT USAGE, CREATE ON SCHEMA public TO {owner}",
            f"GRANT USAGE ON SCHEMA public TO {app}",
            # Extensions need superuser; create them here so migrations can use them.
            "CREATE EXTENSION IF NOT EXISTS vector",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {app}",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
            f"GRANT USAGE, SELECT ON SEQUENCES TO {app}",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
            f"GRANT EXECUTE ON FUNCTIONS TO {app}",
        ]
        for statement in statements:
            await connection.execute(statement)
    finally:
        await connection.close()


async def _upsert_login_role(connection: asyncpg.Connection, role: RoleCredentials) -> None:
    # DDL cannot take bind parameters; let the server quote the password literal.
    password_literal = await connection.fetchval("SELECT quote_literal($1)", role.password)
    exists = await connection.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role.name)
    verb = "ALTER" if exists else "CREATE"
    await connection.execute(f"{verb} ROLE {role.name} LOGIN PASSWORD {password_literal}")
