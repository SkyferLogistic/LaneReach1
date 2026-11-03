"""Alembic environment: async migrations run as the database owner role."""

import asyncio
import logging.config

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from lanereach.core.config import get_settings
from lanereach.core.database import StrEnumType
from lanereach.core.model_registry import load_all_models

config = context.config
if config.config_file_name is not None and not config.attributes.get("skip_logging_config"):
    logging.config.fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = load_all_models()


def _database_url() -> str:
    url = config.attributes.get("database_url")
    return str(url) if url else get_settings().database_owner_url.get_secret_value()


def render_item(type_: str, obj: object, autogen_context: object) -> str | bool:
    # Enum columns are plain VARCHARs in the database; render them as such in migrations.
    if type_ == "type" and isinstance(obj, StrEnumType):
        return f"sa.String(length={obj.impl.length})"  # type: ignore[attr-defined]
    return False


def run_migrations_offline() -> None:
    context.configure(url=_database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _run_sync_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection, target_metadata=target_metadata, render_item=render_item
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_database_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run_sync_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
