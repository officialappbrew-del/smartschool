import argparse
import asyncio
import getpass
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.main import PlatformAdmin, PlatformAdminBase, PlatformAdminLogin, password_hasher


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a platform administrator account."
    )
    parser.add_argument("action", choices=("create",))
    return parser.parse_args()


async def create_admin() -> int:
    database_url = os.getenv("TENANCY_DATABASE_URL")
    if not database_url:
        raise RuntimeError("Set TENANCY_DATABASE_URL in backend/.env")
    secret = os.getenv("TENANCY_JWT_SECRET")
    if secret is None or len(secret) < 32:
        raise RuntimeError("Set a TENANCY_JWT_SECRET of at least 32 characters")

    email = input("Platform admin email: ").strip().lower()
    password = getpass.getpass("Password (at least 12 characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise ValueError("Passwords do not match")
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters")
    credentials = PlatformAdminLogin(email=email, password=password)

    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(PlatformAdminBase.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            session.add(
                PlatformAdmin(
                    email=credentials.email,
                    password_hash=password_hasher.hash(credentials.password),
                    is_active=True,
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                print("A platform administrator with that email already exists.")
                return 1
    finally:
        await engine.dispose()

    print(f"Platform administrator created: {credentials.email}")
    return 0


async def run() -> int:
    arguments = parse_args()
    load_dotenv(Path(__file__).with_name(".env"))
    if arguments.action == "create":
        return await create_admin()
    raise ValueError(f"Unsupported action: {arguments.action}")


# if __name__ == "__main__":
#     raise SystemExit(asyncio.run(run(), loop_factory=asyncio.SelectorEventLoop))

if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
