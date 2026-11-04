import argparse
import asyncio
import io
import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.util.exc import CommandError
from fastapi_tenancy import TenancyManager
from fastapi_tenancy.core.exceptions import (
    MigrationError,
    TenantNotFoundError,
    TenancyError,
)
from fastapi_tenancy.core.types import Tenant
from fastapi_tenancy.migrations.manager import TenantMigrationManager
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.engine import make_url
from dotenv import load_dotenv


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage schema migrations for school tenants.")
    parser.add_argument("action", choices=("upgrade", "downgrade", "current"))
    parser.add_argument("--tenant", help="Run against this one school identifier; default is all.")
    parser.add_argument("--revision", help="Migration target (default: head for upgrade, -1 for downgrade).")
    parser.add_argument("--concurrency", type=int, default=5)
    return parser.parse_args()


def read_current_revision(
    migrator: TenantMigrationManager,
    tenant: Tenant,
) -> str | None:
    config = AlembicConfig("alembic.ini")
    x_args = migrator._build_alembic_args(tenant)
    config.attributes.update(x_args)
    config.attributes["x_args"] = x_args
    output = io.StringIO()
    config.stdout = output
    command.current(config)
    return output.getvalue().strip() or None


async def run() -> int:
    arguments = parse_args()
    if arguments.concurrency < 1:
        raise ValueError("--concurrency must be a positive integer")

    os.chdir(Path(__file__).resolve().parent)
    load_dotenv(Path(__file__).with_name(".env"))
    from app.main import build_tenancy_manager

    manager: TenancyManager | None = build_tenancy_manager()
    if manager is None:
        raise RuntimeError("Set TENANCY_DATABASE_URL before running migrations")
    if make_url(manager.config.database_url).get_backend_name() != "postgresql":
        print(
            "Tenant migrations require PostgreSQL schema isolation. "
            "SQLite uses table-prefix isolation and is supported for tests only.",
            file=sys.stderr,
        )
        await manager.close()
        return 2

    migrator = TenantMigrationManager(
        manager.config,
        manager.store,
        alembic_cfg_path="alembic.ini",
    )
    try:
        await manager.initialize()
        if arguments.tenant:
            try:
                tenant = await manager.store.get_by_identifier(arguments.tenant)
            except TenantNotFoundError:
                print(f"Tenant not found: {arguments.tenant}", file=sys.stderr)
                return 1
            if arguments.action == "current":
                try:
                    revision = await asyncio.to_thread(
                        read_current_revision,
                        migrator,
                        tenant,
                    )
                    print(f"{tenant.identifier}: {revision or 'not migrated'}")
                    return 0
                except (
                    CommandError,
                    MigrationError,
                    SQLAlchemyError,
                    TenancyError,
                    OSError,
                    ValueError,
                ) as error:
                    print(f"FAILED {tenant.identifier}: {error}", file=sys.stderr)
                    return 1
            revision = arguments.revision or (
                "head" if arguments.action == "upgrade" else "-1"
            )
            if arguments.action == "downgrade":
                print(f"WARNING: downgrading {tenant.identifier} to {revision} may discard data.")
            if arguments.action == "upgrade":
                await migrator.upgrade_tenant(tenant, revision=revision)
            else:
                await migrator.downgrade_tenant(tenant, revision=revision)
            print(f"{arguments.action} succeeded for {tenant.identifier}: {revision}")
            return 0

        if arguments.action == "current":
            tenants = []
            offset = 0
            while True:
                page = await manager.store.list(skip=offset, limit=100)
                tenants.extend(page)
                if len(page) < 100:
                    break
                offset += len(page)
            failed = False
            for tenant in tenants:
                try:
                    revision = await asyncio.to_thread(
                        read_current_revision,
                        migrator,
                        tenant,
                    )
                    print(f"{tenant.identifier}: {revision or 'not migrated'}")
                except (
                    CommandError,
                    MigrationError,
                    SQLAlchemyError,
                    TenancyError,
                    OSError,
                    ValueError,
                ) as error:
                    print(f"FAILED {tenant.identifier}: {error}", file=sys.stderr)
                    failed = True
            return 1 if failed else 0

        revision = arguments.revision or ("head" if arguments.action == "upgrade" else "-1")
        if arguments.action == "downgrade":
            print("WARNING: downgrading all school schemas may discard data.")
        if arguments.action == "upgrade":
            results = await migrator.upgrade_all(
                revision=revision,
                concurrency=arguments.concurrency,
            )
        else:
            results = await migrator.downgrade_all(
                revision=revision,
                concurrency=arguments.concurrency,
            )

        failed_results = [result for result in results if not result["success"]]
        print(f"{arguments.action}: {len(results) - len(failed_results)}/{len(results)} succeeded")
        for result in failed_results:
            print(
                f"FAILED {result['identifier']} ({result['tenant_id']}): {result['error']}",
                file=sys.stderr,
            )
        return 1 if failed_results else 0
    finally:
        await manager.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
