import asyncio
import os
import re
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.main import TenantBase


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = TenantBase.metadata


def migration_options(schema: str | None) -> dict[str, object]:
    options: dict[str, object] = {
        "target_metadata": target_metadata,
        "compare_type": True,
    }
    if schema and schema != "public":
        options["version_table_schema"] = schema
    return options


def do_run_migrations(connection, schema: str | None) -> None:
    context.configure(connection=connection, **migration_options(schema))
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    arguments = dict(context.config.attributes.get("x_args", {}))
    arguments.update(context.get_x_argument(as_dictionary=True))
    url = arguments.get("url") or os.getenv("TENANCY_DATABASE_URL")
    if not url:
        raise RuntimeError("Set TENANCY_DATABASE_URL or pass -x url=...")

    schema = arguments.get("schema")
    if schema and not re.fullmatch(r"[a-z][a-z0-9_]*", schema):
        raise ValueError("Unsafe tenant schema name")

    context.configure(
        url=url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **migration_options(schema),
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    arguments = dict(context.config.attributes.get("x_args", {}))
    arguments.update(context.get_x_argument(as_dictionary=True))
    url = arguments.get("url") or os.getenv("TENANCY_DATABASE_URL")
    if not url:
        raise RuntimeError("Set TENANCY_DATABASE_URL or pass -x url=...")

    schema = arguments.get("schema")
    if schema and not re.fullmatch(r"[a-z][a-z0-9_]*", schema):
        raise ValueError("Unsafe tenant schema name")

    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = url
    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        if schema and connection.dialect.name == "postgresql":
            await connection.execute(text(f'SET search_path TO "{schema}", public'))
            await connection.commit()
        await connection.run_sync(do_run_migrations, schema)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
