import asyncio
import hashlib
import hmac
import json
import os
import re
import smtplib
import tempfile
import threading
import time
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
from fastapi import HTTPException
from fastapi.testclient import TestClient
from fastapi_tenancy.core.exceptions import TenantNotFoundError
from fastapi_tenancy.core.types import Tenant, TenantStatus
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool
from sqlalchemy import insert, select

from app.main import (
    DUMMY_PASSWORD_HASH,
    JWT_ISSUER,
    PlatformAuditEvent,
    PlatformAdmin,
    PlatformAdminBase,
    TenantBase,
    TenantUser,
    build_tenancy_manager,
    UserActionToken,
    create_app,
    consume_action_token,
    email_configuration,
    get_request_tenant,
    make_token_hash,
    password_hasher,
    read_tenant_current_revision,
    require_platform_admin,
)


def create_test_app():
    with patch.dict(
        os.environ,
        {
            "TENANCY_DATABASE_URL": "",
            "FRONTEND_ORIGIN": "http://localhost:3000",
            "TENANCY_DOMAIN_SUFFIX": ".localhost",
        },
    ):
        return create_app()


def test_platform_admin() -> PlatformAdmin:
    return PlatformAdmin(
        id="platform-admin-test",
        email="operator@example.test",
        password_hash=DUMMY_PASSWORD_HASH,
        is_active=True,
    )


def attach_platform_database(app):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def initialize() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(PlatformAdminBase.metadata.create_all)

    asyncio.run(initialize())
    app.state.platform_session_factory = session_factory
    return engine


class PlatformAdminAuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = str(Path(self.temporary_directory.name) / "platform-admin.db")
        database_url = f"sqlite+aiosqlite:///{database_path.replace(chr(92), '/')}"
        self.engine = create_async_engine(database_url, poolclass=NullPool)
        self.session_factory = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
        )
        self.password = "platform-admin-password-123"

        async def initialize() -> None:
            async with self.engine.begin() as connection:
                await connection.run_sync(PlatformAdminBase.metadata.create_all)
            async with self.session_factory() as session:
                session.add(
                    PlatformAdmin(
                        id="platform-admin-test",
                        email="operator@example.test",
                        password_hash=password_hasher.hash(self.password),
                        is_active=True,
                    )
                )
                await session.commit()

        asyncio.run(initialize())
        self.environment = patch.dict(
            os.environ,
            {
                "TENANCY_JWT_SECRET": (
                    "platform-auth-test-secret-is-at-least-32-characters"
                ),
            },
        )
        self.environment.start()
        self.app = create_test_app()
        self.app.state.platform_session_factory = self.session_factory
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        asyncio.run(self.engine.dispose())
        self.environment.stop()
        self.temporary_directory.cleanup()

    def login(self) -> str:
        response = self.client.post(
            "/api/v1/admin/auth/login",
            json={
                "email": "OPERATOR@example.test",
                "password": self.password,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["email"], "operator@example.test")
        self.assertEqual(response.json()["expires_in"], 1800)
        return response.json()["access_token"]

    def test_platform_admin_login_issues_short_lived_session(self) -> None:
        token = self.login()
        response = self.client.get(
            "/api/v1/admin/tenants",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            "Tenant administration is not configured",
        )

    def test_invalid_password_is_rejected(self) -> None:
        response = self.client.post(
            "/api/v1/admin/auth/login",
            json={
                "email": "operator@example.test",
                "password": "incorrect-password",
            },
        )
        self.assertEqual(response.status_code, 401)

    def test_admin_routes_require_platform_session(self) -> None:
        response = self.client.get("/api/v1/admin/tenants")
        self.assertEqual(response.status_code, 401)

    def test_school_session_cannot_authenticate_to_platform_dashboard(self) -> None:
        school_token = jwt.encode(
            {
                "sub": "school-admin",
                "tenant_id": "greenwood",
                "role": "school_admin",
                "iss": JWT_ISSUER,
                "exp": datetime.now(UTC) + timedelta(minutes=30),
            },
            "platform-auth-test-secret-is-at-least-32-characters",
            algorithm="HS256",
        )
        response = self.client.get(
            "/api/v1/admin/tenants",
            headers={"Authorization": f"Bearer {school_token}"},
        )
        self.assertEqual(response.status_code, 401)

    def test_deactivated_admin_session_is_revoked(self) -> None:
        token = self.login()

        async def deactivate() -> None:
            async with self.session_factory() as session:
                admin = await session.get(PlatformAdmin, "platform-admin-test")
                assert admin is not None
                admin.is_active = False
                await session.commit()

        asyncio.run(deactivate())
        response = self.client.get(
            "/api/v1/admin/tenants",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 401)


def session_result(row: dict[str, object] | None = None) -> MagicMock:
    result = MagicMock()
    result.mappings.return_value.one_or_none.return_value = row
    return result


class TenantRegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_test_app()
        self.platform_engine = attach_platform_database(self.app)
        self.manager = SimpleNamespace(
            register_tenant=AsyncMock(
                return_value=SimpleNamespace(
                    identifier="greenwood",
                    name="Greenwood High School",
                )
            )
        )
        self.app.state.tenancy_manager = self.manager
        self.app.dependency_overrides[require_platform_admin] = test_platform_admin
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        asyncio.run(self.platform_engine.dispose())

    def test_registration_creates_tenant_with_school_metadata(self) -> None:
        response = self.client.post(
            "/api/v1/admin/tenants",
            json={"identifier": "greenwood", "name": "Greenwood High School"},
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.json(),
            {"identifier": "greenwood", "name": "Greenwood High School"},
        )
        self.manager.register_tenant.assert_awaited_once()
        registered = self.manager.register_tenant.await_args.kwargs
        self.assertEqual(registered["identifier"], "greenwood")
        self.assertEqual(registered["name"], "Greenwood High School")
        self.assertIs(registered["app_metadata"], TenantBase.metadata)
        self.assertIn("school_profile", TenantBase.metadata.tables)
        activity = self.client.get("/api/v1/admin/activity")
        self.assertEqual(activity.status_code, 200)
        self.assertEqual(activity.json()["items"][0]["action"], "tenant_created")
        self.assertEqual(
            activity.json()["items"][0]["tenant_identifier"],
            "greenwood",
        )

    def test_reserved_subdomains_are_rejected(self) -> None:
        response = self.client.post(
            "/api/v1/admin/tenants",
            json={"identifier": "api", "name": "Reserved school"},
        )

        self.assertEqual(response.status_code, 422)
        self.manager.register_tenant.assert_not_awaited()

    def test_existing_tenant_returns_conflict(self) -> None:
        self.manager.register_tenant.side_effect = ValueError("identifier already taken")

        response = self.client.post(
            "/api/v1/admin/tenants",
            json={"identifier": "greenwood", "name": "Greenwood High School"},
        )

        self.assertEqual(response.status_code, 409)

    def test_identifier_must_be_a_safe_subdomain_slug(self) -> None:
        response = self.client.post(
            "/api/v1/admin/tenants",
            json={"identifier": "Greenwood High", "name": "Greenwood High School"},
        )

        self.assertEqual(response.status_code, 422)
        self.manager.register_tenant.assert_not_awaited()


class PlatformTenantManagementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_test_app()
        self.platform_engine = attach_platform_database(self.app)
        self.tenant = SimpleNamespace(
            id="tenant-id",
            identifier="greenwood",
            name="Greenwood High School",
            status=TenantStatus.ACTIVE,
        )
        self.store = SimpleNamespace(
            list=AsyncMock(return_value=[self.tenant]),
            count=AsyncMock(return_value=1),
            get_by_identifier=AsyncMock(return_value=self.tenant),
        )
        self.manager = SimpleNamespace(
            store=self.store,
            config=SimpleNamespace(),
            set_status=AsyncMock(
                return_value=SimpleNamespace(
                    identifier="greenwood",
                    name="Greenwood High School",
                    status=TenantStatus.SUSPENDED,
                )
            ),
        )
        self.app.state.tenancy_manager = self.manager
        self.app.dependency_overrides[require_platform_admin] = test_platform_admin
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        asyncio.run(self.platform_engine.dispose())

    @patch("app.main.TenancyManager")
    @patch("app.main.SubdomainTenantResolver")
    @patch("app.main.SQLAlchemyTenantStore")
    def test_synchronous_postgres_url_is_normalized_for_async_database_access(
        self,
        store_factory,
        _resolver_factory,
        _manager_factory,
    ) -> None:
        with patch.dict(
            os.environ,
            {"TENANCY_DATABASE_URL": "postgresql://user:password@localhost/northstar"},
        ):
            build_tenancy_manager()

        self.assertEqual(
            str(store_factory.call_args.args[0]),
            "postgresql+asyncpg://user:password@localhost/northstar",
        )

    def test_revision_reader_extracts_the_applied_alembic_revision(self) -> None:
        migrator = SimpleNamespace(
            _build_alembic_args=lambda tenant: {"tenant_identifier": tenant.identifier},
        )
        tenant = SimpleNamespace(identifier="greenwood")

        def write_current_revision(config) -> None:
            config.stdout.write("tenant_greenwood: 0007_academic_terms (head)\n")

        with patch("app.main.command.current", side_effect=write_current_revision):
            revision = read_tenant_current_revision(migrator, tenant)

        self.assertEqual(revision, "0007_academic_terms")

    def test_revision_reader_serializes_alembic_commands(self) -> None:
        migrator = SimpleNamespace(
            _build_alembic_args=lambda tenant: {"tenant_identifier": tenant.identifier},
        )
        tenants = [SimpleNamespace(identifier="greenwood"), SimpleNamespace(identifier="oakwood")]
        state_lock = threading.Lock()
        active_commands = 0
        max_active_commands = 0

        def write_current_revision(config) -> None:
            nonlocal active_commands, max_active_commands
            with state_lock:
                active_commands += 1
                max_active_commands = max(max_active_commands, active_commands)
            time.sleep(0.02)
            config.stdout.write("0007_academic_terms (head)\n")
            with state_lock:
                active_commands -= 1

        with patch("app.main.command.current", side_effect=write_current_revision):
            with ThreadPoolExecutor(max_workers=2) as executor:
                revisions = list(
                    executor.map(
                        lambda tenant: read_tenant_current_revision(migrator, tenant),
                        tenants,
                    )
                )

        self.assertEqual(revisions, ["0007_academic_terms", "0007_academic_terms"])
        self.assertEqual(max_active_commands, 1)

    def test_create_app_loads_backend_env_file(self) -> None:
        with patch("app.main.build_tenancy_manager", return_value=None), patch(
            "app.main.load_dotenv"
        ) as load_dotenv:
            create_app()

        load_dotenv.assert_called_once_with(
            Path(__file__).resolve().parents[1] / ".env",
            override=False,
        )

    def test_admin_subdomain_passes_cors_preflight(self) -> None:
        with patch.dict(
            os.environ,
            {
                "FRONTEND_ORIGIN": "http://localhost:5173",
                "TENANCY_DOMAIN_SUFFIX": ".localhost",
            },
        ), patch("app.main.build_tenancy_manager", return_value=None):
            app = create_app()

        with TestClient(app) as client:
            response = client.options(
                "/api/v1/admin/tenants?skip=0&limit=50",
                headers={
                    "Origin": "http://admin.localhost:5173",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "authorization",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["access-control-allow-origin"],
            "http://admin.localhost:5173",
        )

    def test_suspended_tenant_context_cannot_access_school_api(self) -> None:
        suspended_tenant = Tenant(
            id="tenant-id",
            identifier="greenwood",
            name="Greenwood High School",
            status=TenantStatus.SUSPENDED,
        )
        request = SimpleNamespace(
            state=SimpleNamespace(tenant=suspended_tenant),
        )
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(get_request_tenant(request))
        self.assertEqual(raised.exception.status_code, 403)

    def test_tenant_list_returns_paged_tenant_summaries(self) -> None:
        with patch(
            "app.main.read_tenant_current_revision",
            return_value="0017_report_card_publication",
        ):
            response = self.client.get(
                "/api/v1/admin/tenants?skip=0&limit=50",
            )
        self.assertEqual(response.status_code, 200)
        item = response.json()["items"][0]
        self.assertEqual(item["identifier"], "greenwood")
        self.assertEqual(item["name"], "Greenwood High School")
        self.assertEqual(item["status"], "active")
        self.assertEqual(item["migration_status"], "current")
        self.assertEqual(item["current_revision"], item["head_revision"])
        self.assertEqual(response.json()["total"], 1)

    def test_tenant_migration_status_is_unknown_when_revision_check_fails(self) -> None:
        with patch(
            "app.main.read_tenant_current_revision",
            side_effect=RuntimeError("database unavailable"),
        ):
            response = self.client.get(
                "/api/v1/admin/tenants",
                headers={"Origin": "http://admin.localhost:3000"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["access-control-allow-origin"],
            "http://admin.localhost:3000",
        )
        item = response.json()["items"][0]
        self.assertEqual(item["migration_status"], "unknown")
        self.assertIsNone(item["current_revision"])

    def test_platform_activity_lists_persisted_admin_actions(self) -> None:
        async def create_event() -> None:
            async with self.app.state.platform_session_factory() as session:
                session.add(
                    PlatformAuditEvent(
                        id="audit-event-test",
                        admin_id="platform-admin-test",
                        admin_email="operator@example.test",
                        action="tenant_created",
                        tenant_identifier="greenwood",
                        summary="Created Greenwood High School",
                    )
                )
                await session.commit()

        asyncio.run(create_event())
        response = self.client.get("/api/v1/admin/activity")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["items"]), 1)
        self.assertEqual(response.json()["items"][0]["action"], "tenant_created")
        self.assertEqual(
            response.json()["items"][0]["tenant_identifier"],
            "greenwood",
        )

    def test_platform_admin_can_suspend_tenant(self) -> None:
        response = self.client.patch(
            "/api/v1/admin/tenants/greenwood/status",
            json={"status": "suspended"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "suspended")
        self.manager.set_status.assert_awaited_once_with(
            "tenant-id",
            TenantStatus.SUSPENDED,
        )

    @patch("app.main.TenantMigrationManager")
    def test_platform_admin_can_run_tenant_migrations(self, migrator_factory) -> None:
        migrator = migrator_factory.return_value
        migrator.upgrade_tenant = AsyncMock()
        response = self.client.post(
            "/api/v1/admin/tenants/greenwood/migrations",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], "Tenant database is up to date.")
        migrator.upgrade_tenant.assert_awaited_once_with(
            self.tenant,
            revision="head",
        )


class TenantAdminProvisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_test_app()
        self.platform_engine = attach_platform_database(self.app)
        self.app.dependency_overrides[require_platform_admin] = test_platform_admin
        self.client = TestClient(self.app)
        self.session = SimpleNamespace(
            info={},
            execute=AsyncMock(return_value=session_result()),
            commit=AsyncMock(),
            rollback=AsyncMock(),
        )
        self.tenant = SimpleNamespace(
            identifier="greenwood",
            name="Greenwood High School",
            status=TenantStatus.ACTIVE,
        )

        @asynccontextmanager
        async def session_context(_tenant: object):
            assert _tenant is self.tenant
            yield self.session

        self.manager = SimpleNamespace(
            store=SimpleNamespace(get_by_identifier=AsyncMock(return_value=self.tenant)),
            isolation_provider=SimpleNamespace(get_session=session_context),
        )
        self.app.state.tenancy_manager = self.manager
        self.email_configuration = patch.dict(
            os.environ,
            {
                "SMTP_HOST": "smtp.example.test",
                "SMTP_FROM": "noreply@example.test",
                "TENANCY_DOMAIN_SUFFIX": ".localhost",
                "PUBLIC_APP_BASE_URL": "http://localhost:3000",
            },
        )
        self.email_configuration.start()
        self.deliver_email = patch("app.main.deliver_action_email", new_callable=AsyncMock)
        self.send_email = self.deliver_email.start()

    def tearDown(self) -> None:
        self.deliver_email.stop()
        self.email_configuration.stop()
        self.client.close()
        asyncio.run(self.platform_engine.dispose())

    def test_suspended_tenant_cannot_receive_an_admin_invitation(self) -> None:
        self.tenant.status = TenantStatus.SUSPENDED
        response = self.client.post(
            "/api/v1/admin/tenants/greenwood/admin-invitations",
            json={"email": "principal@example.com"},
        )

        self.assertEqual(response.status_code, 409)
        self.session.execute.assert_not_awaited()
        self.send_email.assert_not_awaited()

    def test_platform_admin_sends_single_use_school_admin_invitation(self) -> None:
        response = self.client.post(
            "/api/v1/admin/tenants/greenwood/admin-invitations",
            json={"email": "Principal@Example.com"},
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {"message": "Invitation email sent"})
        self.assertTrue(self.session.execute.await_count >= 4)
        inserted = self.session.execute.await_args_list[1].args[0].compile().params
        self.assertEqual(inserted["email"], "principal@example.com")
        self.assertFalse(inserted["is_active"])
        self.assertEqual(inserted["role"], "school_admin")
        self.assertEqual(inserted["password_hash"], DUMMY_PASSWORD_HASH)
        self.send_email.assert_awaited_once()
        self.assertIn("greenwood.localhost:3000/invitation/accept?token=", self.send_email.await_args.args[2])
        self.session.commit.assert_awaited_once()

    def test_failed_invitation_delivery_revokes_the_token(self) -> None:
        self.send_email.side_effect = smtplib.SMTPException("SMTP delivery failed")

        response = self.client.post(
            "/api/v1/admin/tenants/greenwood/admin-invitations",
            json={"email": "principal@example.com"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "Invitation email could not be delivered")
        self.assertEqual(self.session.execute.await_count, 5)
        self.assertEqual(self.session.commit.await_count, 2)

    def test_existing_tenant_admin_email_returns_conflict(self) -> None:
        self.session.execute.return_value = session_result(
            {
                "id": "existing-user",
                "email": "principal@example.com",
                "password_hash": "unused",
                "role": "school_admin",
                "is_active": True,
                "created_at": None,
            }
        )

        response = self.client.post(
            "/api/v1/admin/tenants/greenwood/admin-invitations",
            json={"email": "principal@example.com"},
        )

        self.assertEqual(response.status_code, 409)
        self.session.commit.assert_not_awaited()

    def test_unknown_tenant_does_not_create_an_admin(self) -> None:
        self.manager.store.get_by_identifier.side_effect = TenantNotFoundError("unknown-school")

        response = self.client.post(
            "/api/v1/admin/tenants/unknown-school/admin-invitations",
            json={"email": "principal@example.com"},
        )

        self.assertEqual(response.status_code, 404)
        self.session.execute.assert_not_awaited()


class SchoolAuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_test_app()
        self.client = TestClient(self.app)
        self.tenant = SimpleNamespace(identifier="greenwood")
        self.user = TenantUser(
            id="school-admin-id",
            email="principal@example.com",
            password_hash=password_hasher.hash("a-strong-password-123"),
            role="school_admin",
            is_active=True,
        )
        self.user_row = {
            "id": self.user.id,
            "email": self.user.email,
            "password_hash": self.user.password_hash,
            "role": self.user.role,
            "is_active": self.user.is_active,
            "created_at": None,
        }
        self.session = SimpleNamespace(
            info={},
            execute=AsyncMock(return_value=session_result(self.user_row)),
            commit=AsyncMock(),
            rollback=AsyncMock(),
        )

        async def get_session():
            return self.session

        self.app.dependency_overrides[get_request_tenant] = lambda: self.tenant
        self.app.dependency_overrides[self.app.state.tenant_db_dependency] = get_session
        self.environment = patch.dict(
            os.environ,
            {
                "TENANCY_JWT_SECRET": "test-secret-that-is-at-least-32-characters-long",
                "SMTP_HOST": "smtp.example.test",
                "SMTP_FROM": "noreply@example.test",
                "TENANCY_DOMAIN_SUFFIX": ".localhost",
                "PUBLIC_APP_BASE_URL": "http://localhost:3000",
            },
        )
        self.environment.start()
        self.deliver_email = patch("app.main.deliver_action_email", new_callable=AsyncMock)
        self.send_email = self.deliver_email.start()

    def tearDown(self) -> None:
        self.deliver_email.stop()
        self.environment.stop()
        self.client.close()

    def test_login_returns_tenant_bound_access_token(self) -> None:
        response = self.client.post(
            "/api/v1/auth/login",
            json={"email": "Principal@Example.com", "password": "a-strong-password-123"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["token_type"], "bearer")
        import jwt

        claims = jwt.decode(
            response.json()["access_token"],
            "test-secret-that-is-at-least-32-characters-long",
            algorithms=["HS256"],
            issuer="northstar-school-api",
        )
        self.assertEqual(claims["tenant_id"], "greenwood")
        self.assertEqual(claims["sub"], self.user.id)

    def test_login_does_not_reveal_unknown_account(self) -> None:
        self.session.execute.return_value = session_result()

        response = self.client.post(
            "/api/v1/auth/login",
            json={"email": "unknown@example.com", "password": "a-strong-password-123"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Invalid email or password")

    def test_school_token_cannot_be_used_on_another_tenant_subdomain(self) -> None:
        token = self.client.post(
            "/api/v1/auth/login",
            json={"email": "principal@example.com", "password": "a-strong-password-123"},
        ).json()["access_token"]
        self.tenant.identifier = "oakwood"
        response = self.client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Tenant mismatch")
        self.assertEqual(self.session.execute.await_count, 1)

    def test_school_admin_can_change_their_password(self) -> None:
        token = self.client.post(
            "/api/v1/auth/login",
            json={"email": "principal@example.com", "password": "a-strong-password-123"},
        ).json()["access_token"]
        response = self.client.post(
            "/api/v1/auth/change-password",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "current_password": "a-strong-password-123",
                "new_password": "a-different-password-456",
            },
        )

        self.assertEqual(response.status_code, 204)
        updated_password_hash = self.session.execute.await_args_list[-1].args[0].compile().params[
            "password_hash"
        ]
        self.assertTrue(password_hasher.verify("a-different-password-456", updated_password_hash))
        self.session.commit.assert_awaited_once()

    def test_password_change_requires_the_current_password(self) -> None:
        token = self.client.post(
            "/api/v1/auth/login",
            json={"email": "principal@example.com", "password": "a-strong-password-123"},
        ).json()["access_token"]
        response = self.client.post(
            "/api/v1/auth/change-password",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "current_password": "incorrect-current-password",
                "new_password": "a-different-password-456",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.session.commit.assert_not_awaited()

    def test_password_reset_request_sends_email_without_disclosing_account(self) -> None:
        response = self.client.post(
            "/api/v1/auth/password-reset/request",
            json={"email": "Principal@Example.com"},
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.json()["message"],
            "If the account exists, password reset instructions will be sent.",
        )
        self.send_email.assert_awaited_once()
        self.assertEqual(self.send_email.await_args.args[0], "principal@example.com")
        self.assertIn("greenwood.localhost:3000/password/reset?token=", self.send_email.await_args.args[2])

    def test_password_reset_request_has_same_response_for_unknown_account(self) -> None:
        self.session.execute.return_value = session_result()

        response = self.client.post(
            "/api/v1/auth/password-reset/request",
            json={"email": "unknown@example.com"},
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.json()["message"],
            "If the account exists, password reset instructions will be sent.",
        )
        self.send_email.assert_not_awaited()

    def test_invitation_acceptance_sets_password_and_activates_account(self) -> None:
        consumed_token = MagicMock()
        consumed_token.scalar_one_or_none.return_value = self.user.id
        activated_user = MagicMock()
        activated_user.rowcount = 1
        self.session.execute.side_effect = [consumed_token, activated_user]

        response = self.client.post(
            "/api/v1/auth/invitations/accept",
            json={"token": "invitation-token-value-that-is-long-enough", "password": "a-new-password-123"},
        )

        self.assertEqual(response.status_code, 200)
        self.session.commit.assert_awaited_once()
        values = self.session.execute.await_args_list[1].args[0].compile().params
        self.assertTrue(password_hasher.verify("a-new-password-123", values["password_hash"]))
        self.assertTrue(values["is_active"])

    def test_expired_or_reused_invitation_token_is_rejected(self) -> None:
        consumed_token = MagicMock()
        consumed_token.scalar_one_or_none.return_value = None
        self.session.execute.return_value = consumed_token

        response = self.client.post(
            "/api/v1/auth/invitations/accept",
            json={"token": "invitation-token-value-that-is-long-enough", "password": "a-new-password-123"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Invitation link is invalid or expired")
        self.session.commit.assert_not_awaited()

    def test_password_reset_confirmation_updates_password_and_consumes_token(self) -> None:
        consumed_token = MagicMock()
        consumed_token.scalar_one_or_none.return_value = self.user.id
        updated_user = MagicMock()
        updated_user.rowcount = 1
        self.session.execute.side_effect = [consumed_token, updated_user, MagicMock()]

        response = self.client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": "password-reset-token-value-is-long-enough", "password": "a-new-password-123"},
        )

        self.assertEqual(response.status_code, 200)
        self.session.commit.assert_awaited_once()
        values = self.session.execute.await_args_list[1].args[0].compile().params
        self.assertTrue(password_hasher.verify("a-new-password-123", values["password_hash"]))

    def test_invalid_password_reset_token_is_rejected(self) -> None:
        consumed_token = MagicMock()
        consumed_token.scalar_one_or_none.return_value = None
        self.session.execute.return_value = consumed_token

        response = self.client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": "password-reset-token-value-is-long-enough", "password": "a-new-password-123"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Password reset link is invalid or expired")
        self.session.commit.assert_not_awaited()

    def test_email_settings_accept_gmail_configuration_names(self) -> None:
        with patch.dict(
            os.environ,
            {
                "EMAIL_DELIVERY_MODE": "sync",
                "EMAIL_HOST": "smtp.gmail.com",
                "EMAIL_PORT": "587",
                "EMAIL_USE_TLS": "True",
                "EMAIL_USE_SSL": "False",
                "EMAIL_HOST_USER": "mailer@example.com",
                "EMAIL_HOST_PASSWORD": "test-app-password",
                "DEFAULT_FROM_EMAIL": "mailer@example.com",
            },
            clear=True,
        ):
            self.assertEqual(
                email_configuration(),
                (
                    "smtp.gmail.com",
                    "mailer@example.com",
                    587,
                    "mailer@example.com",
                    "test-app-password",
                    False,
                    True,
                ),
            )

    def test_email_settings_reject_celery_and_conflicting_tls_modes(self) -> None:
        with patch.dict(os.environ, {"EMAIL_DELIVERY_MODE": "async"}, clear=True):
            with self.assertRaisesRegex(ValueError, "Celery delivery is not configured"):
                email_configuration()

        with patch.dict(
            os.environ,
            {
                "EMAIL_HOST": "smtp.gmail.com",
                "DEFAULT_FROM_EMAIL": "mailer@example.com",
                "EMAIL_USE_TLS": "true",
                "EMAIL_USE_SSL": "true",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "cannot both be enabled"):
                email_configuration()


class TenantIsolationIntegrationTests(unittest.TestCase):
    def test_tenant_admins_and_access_tokens_are_isolated_by_subdomain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = str(Path(temporary_directory) / "northstar.db").replace("\\", "/")
            environment = patch.dict(
                os.environ,
                {
                    "TENANCY_DATABASE_URL": f"sqlite+aiosqlite:///{database_path}",
                    "TENANCY_DOMAIN_SUFFIX": ".localhost",
                    "TENANCY_CACHE_ENABLED": "false",
                    "TENANCY_JWT_SECRET": "integration-test-secret-at-least-32-characters",
                    "BILLING_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
                    "FRONTEND_ORIGIN": "http://localhost:3000",
                    "SMTP_HOST": "smtp.example.test",
                    "SMTP_FROM": "noreply@example.test",
                    "PUBLIC_APP_BASE_URL": "http://localhost:3000",
                },
            )
            environment.start()
            mail_delivery = patch("app.main.deliver_action_email", new_callable=AsyncMock)
            send_email = mail_delivery.start()
            try:
                app = create_app()
                app.dependency_overrides[require_platform_admin] = test_platform_admin
                with TestClient(app) as client:
                    cors_response = client.options(
                        "/api/v1/auth/login",
                        headers={
                            "host": "greenwood.localhost",
                            "origin": "http://greenwood.localhost:3000",
                            "access-control-request-method": "POST",
                            "access-control-request-headers": "content-type",
                        },
                    )
                    self.assertEqual(cors_response.status_code, 200, cors_response.text)
                    self.assertEqual(
                        cors_response.headers.get("access-control-allow-origin"),
                        "http://greenwood.localhost:3000",
                    )

                    for identifier in ("greenwood", "oakwood"):
                        response = client.post(
                            "/api/v1/admin/tenants",
                            json={"identifier": identifier, "name": f"{identifier.title()} School"},
                        )
                        self.assertEqual(response.status_code, 201, response.text)

                    response = client.post(
                        "/api/v1/admin/tenants/greenwood/admin-invitations",
                        json={"email": "principal@example.com"},
                    )
                    self.assertEqual(response.status_code, 202, response.text)

                    invitation_body = send_email.await_args.args[2]
                    invitation_link = re.search(r"https?://[^\s]+", invitation_body)
                    self.assertIsNotNone(invitation_link)
                    invitation_token = parse_qs(
                        urlsplit(invitation_link.group(0)).query
                    )["token"][0]

                    greenwood_headers = {
                        "host": "greenwood.localhost",
                        "x-forwarded-host": "oakwood.localhost",
                    }
                    oakwood_headers = {
                        "host": "oakwood.localhost",
                        "x-forwarded-host": "greenwood.localhost",
                    }
                    acceptance = client.post(
                        "/api/v1/auth/invitations/accept",
                        headers=greenwood_headers,
                        json={
                            "token": invitation_token,
                            "password": "integration-test-password",
                        },
                    )
                    self.assertEqual(acceptance.status_code, 200, acceptance.text)
                    replayed_invitation = client.post(
                        "/api/v1/auth/invitations/accept",
                        headers=greenwood_headers,
                        json={
                            "token": invitation_token,
                            "password": "another-integration-password",
                        },
                    )
                    self.assertEqual(replayed_invitation.status_code, 400)

                    login_response = client.post(
                        "/api/v1/auth/login",
                        headers=greenwood_headers,
                        json={
                            "email": "principal@example.com",
                            "password": "integration-test-password",
                        },
                    )
                    self.assertEqual(login_response.status_code, 200, login_response.text)
                    token = login_response.json()["access_token"]

                    own_account = client.get(
                        "/api/v1/auth/me",
                        headers={**greenwood_headers, "Authorization": f"Bearer {token}"},
                    )
                    self.assertEqual(own_account.status_code, 200, own_account.text)
                    self.assertEqual(own_account.json()["email"], "principal@example.com")

                    unauthenticated_students = client.get(
                        "/api/v1/students",
                        headers=greenwood_headers,
                    )
                    self.assertEqual(unauthenticated_students.status_code, 401)

                    student_headers = {
                        **greenwood_headers,
                        "Authorization": f"Bearer {token}",
                    }
                    academic_year = client.post(
                        "/api/v1/academic-years",
                        headers=student_headers,
                        json={"name": "2026-2027", "is_current": True},
                    )
                    self.assertEqual(academic_year.status_code, 201, academic_year.text)
                    initial_class = client.post(
                        "/api/v1/classes",
                        headers=student_headers,
                        json={
                            "name": "Grade 5A",
                            "academic_year_id": academic_year.json()["id"],
                        },
                    )
                    self.assertEqual(initial_class.status_code, 201, initial_class.text)
                    admitted_student = client.post(
                        "/api/v1/students",
                        headers=student_headers,
                        json={
                            "admission_number": "gr-001",
                            "first_name": "Amina",
                            "last_name": "Okafor",
                            "date_of_birth": "2015-05-04",
                            "gender": "female",
                            "class_section_id": initial_class.json()["id"],
                            "guardian_name": "Ada Okafor",
                            "guardian_email": "ADA@example.com",
                            "guardian_phone": "+2348000000000",
                        },
                    )
                    self.assertEqual(admitted_student.status_code, 201, admitted_student.text)
                    self.assertEqual(admitted_student.json()["admission_number"], "GR-001")
                    self.assertEqual(admitted_student.json()["guardian_email"], "ada@example.com")

                    guardian_invitation = client.post(
                        f"/api/v1/students/{admitted_student.json()['id']}/guardian-invitation",
                        headers=student_headers,
                    )
                    self.assertEqual(guardian_invitation.status_code, 202, guardian_invitation.text)
                    guardian_invitation_body = send_email.await_args.args[2]
                    guardian_invitation_link = re.search(
                        r"https?://[^\s]+",
                        guardian_invitation_body,
                    )
                    self.assertIsNotNone(guardian_invitation_link)
                    guardian_invitation_token = parse_qs(
                        urlsplit(guardian_invitation_link.group(0)).query
                    )["token"][0]
                    guardian_acceptance = client.post(
                        "/api/v1/auth/invitations/accept",
                        headers=greenwood_headers,
                        json={
                            "token": guardian_invitation_token,
                            "password": "guardian-test-password-123",
                        },
                    )
                    self.assertEqual(guardian_acceptance.status_code, 200, guardian_acceptance.text)
                    guardian_login = client.post(
                        "/api/v1/auth/login",
                        headers=greenwood_headers,
                        json={
                            "email": "ada@example.com",
                            "password": "guardian-test-password-123",
                        },
                    )
                    self.assertEqual(guardian_login.status_code, 200, guardian_login.text)
                    self.assertEqual(guardian_login.json()["role"], "guardian")
                    sent_email_count = send_email.await_count
                    repeated_guardian_invitation = client.post(
                        f"/api/v1/students/{admitted_student.json()['id']}/guardian-invitation",
                        headers=student_headers,
                    )
                    self.assertEqual(repeated_guardian_invitation.status_code, 202)
                    self.assertEqual(send_email.await_count, sent_email_count)
                    guardian_headers = {
                        **greenwood_headers,
                        "Authorization": f"Bearer {guardian_login.json()['access_token']}",
                    }
                    linked_children = client.get(
                        "/api/v1/parents/children",
                        headers=guardian_headers,
                    )
                    self.assertEqual(linked_children.status_code, 200, linked_children.text)
                    self.assertEqual(len(linked_children.json()), 1)
                    self.assertEqual(linked_children.json()[0]["admission_number"], "GR-001")
                    self.assertNotIn("medical_notes", linked_children.json()[0])

                    announcement_draft = client.post(
                        "/api/v1/announcements",
                        headers=student_headers,
                        json={
                            "title": "School closes early",
                            "body": "Classes end at noon this Friday.",
                        },
                    )
                    self.assertEqual(announcement_draft.status_code, 201, announcement_draft.text)
                    self.assertEqual(announcement_draft.json()["status"], "draft")
                    self.assertEqual(
                        client.get("/api/v1/parents/announcements", headers=guardian_headers).json()["total"],
                        0,
                    )
                    self.assertEqual(
                        client.post(
                            f"/api/v1/announcements/{announcement_draft.json()['id']}/publish",
                            headers=guardian_headers,
                        ).status_code,
                        403,
                    )
                    published_announcement = client.post(
                        f"/api/v1/announcements/{announcement_draft.json()['id']}/publish",
                        headers=student_headers,
                    )
                    self.assertEqual(published_announcement.status_code, 200, published_announcement.text)
                    self.assertEqual(published_announcement.json()["recipient_count"], 1)
                    self.assertEqual(published_announcement.json()["delivered_count"], 1)
                    self.assertEqual(send_email.await_args.args[0], "ada@example.com")
                    parent_announcements = client.get(
                        "/api/v1/parents/announcements",
                        headers=guardian_headers,
                    )
                    self.assertEqual(parent_announcements.status_code, 200, parent_announcements.text)
                    self.assertEqual(parent_announcements.json()["total"], 1)
                    self.assertEqual(
                        parent_announcements.json()["items"][0]["title"],
                        "School closes early",
                    )
                    self.assertNotIn("recipient_count", parent_announcements.json()["items"][0])
                    self.assertEqual(
                        client.post(
                            f"/api/v1/announcements/{announcement_draft.json()['id']}/publish",
                            headers=student_headers,
                        ).status_code,
                        409,
                    )
                    self.assertEqual(
                        client.get(
                            "/api/v1/parents/announcements",
                            headers={
                                **oakwood_headers,
                                "Authorization": guardian_headers["Authorization"],
                            },
                        ).status_code,
                        403,
                    )

                    retry_draft = client.post(
                        "/api/v1/announcements",
                        headers=student_headers,
                        json={"title": "Sports day", "body": "Sports day is next week."},
                    )
                    self.assertEqual(retry_draft.status_code, 201, retry_draft.text)
                    send_email.side_effect = [RuntimeError("SMTP unavailable"), None]
                    failed_delivery = client.post(
                        f"/api/v1/announcements/{retry_draft.json()['id']}/publish",
                        headers=student_headers,
                    )
                    self.assertEqual(failed_delivery.status_code, 200, failed_delivery.text)
                    self.assertEqual(failed_delivery.json()["failed_count"], 1)
                    retried_delivery = client.post(
                        f"/api/v1/announcements/{retry_draft.json()['id']}/deliveries/retry",
                        headers=student_headers,
                    )
                    self.assertEqual(retried_delivery.status_code, 200, retried_delivery.text)
                    self.assertEqual(retried_delivery.json()["failed_count"], 0)
                    self.assertEqual(retried_delivery.json()["delivered_count"], 1)
                    send_email.side_effect = None

                    self.assertEqual(
                        client.get("/api/v1/students", headers=guardian_headers).status_code,
                        403,
                    )

                    classes = client.get("/api/v1/classes", headers=student_headers)
                    self.assertEqual(classes.status_code, 200, classes.text)
                    self.assertEqual(len(classes.json()), 1)
                    self.assertEqual(classes.json()[0]["name"], "Grade 5A")
                    self.assertEqual(classes.json()[0]["student_count"], 1)

                    enrollment_url = (
                        f"/api/v1/students/{admitted_student.json()['id']}/enrollments"
                    )
                    enrollment_history = client.get(
                        enrollment_url,
                        headers=student_headers,
                    )
                    self.assertEqual(enrollment_history.status_code, 200)
                    self.assertEqual(len(enrollment_history.json()), 1)
                    self.assertEqual(enrollment_history.json()[0]["status"], "active")

                    academic_years = client.get(
                        "/api/v1/academic-years",
                        headers=student_headers,
                    )
                    self.assertEqual(academic_years.status_code, 200)
                    self.assertEqual(academic_years.json()[0]["name"], "2026-2027")
                    self.assertEqual(academic_years.json()[0]["class_count"], 1)

                    invalid_academic_year = client.post(
                        "/api/v1/academic-years",
                        headers=student_headers,
                        json={
                            "name": "2027-2028",
                            "start_date": "2028-08-01",
                            "end_date": "2027-06-30",
                        },
                    )
                    self.assertEqual(invalid_academic_year.status_code, 422)

                    created_academic_year = client.post(
                        "/api/v1/academic-years",
                        headers=student_headers,
                        json={
                            "name": "2027-2028",
                            "start_date": "2027-08-01",
                            "end_date": "2028-06-30",
                            "is_current": True,
                        },
                    )
                    self.assertEqual(
                        created_academic_year.status_code,
                        201,
                        created_academic_year.text,
                    )
                    self.assertTrue(created_academic_year.json()["is_current"])
                    academic_years = client.get(
                        "/api/v1/academic-years",
                        headers=student_headers,
                    )
                    self.assertEqual(
                        {
                            year["name"]: year["is_current"]
                            for year in academic_years.json()
                        },
                        {"2026-2027": False, "2027-2028": True},
                    )
                    duplicate_academic_year = client.post(
                        "/api/v1/academic-years",
                        headers=student_headers,
                        json={"name": "2027-2028"},
                    )
                    self.assertEqual(duplicate_academic_year.status_code, 409)

                    new_class = client.post(
                        "/api/v1/classes",
                        headers=student_headers,
                        json={"name": "Grade 5B", "academic_year": "2026-2027"},
                    )
                    self.assertEqual(new_class.status_code, 201, new_class.text)
                    duplicate_class = client.post(
                        "/api/v1/classes",
                        headers=student_headers,
                        json={"name": "Grade 5B", "academic_year": "2026-2027"},
                    )
                    self.assertEqual(duplicate_class.status_code, 409)

                    transfer = client.post(
                        enrollment_url,
                        headers=student_headers,
                        json={"class_section_id": new_class.json()["id"]},
                    )
                    self.assertEqual(transfer.status_code, 201, transfer.text)
                    self.assertEqual(transfer.json()["class_name"], "Grade 5B")
                    enrollment_history = client.get(
                        enrollment_url,
                        headers=student_headers,
                    )
                    self.assertEqual(
                        {
                            (item["class_name"], item["status"])
                            for item in enrollment_history.json()
                        },
                        {("Grade 5A", "transferred"), ("Grade 5B", "active")},
                    )
                    attendance_day = datetime.now(UTC).date()
                    attendance_date = attendance_day.isoformat()
                    all_classes = client.get(
                        "/api/v1/classes",
                        headers=student_headers,
                    )
                    unassigned_class_id = next(
                        item["id"]
                        for item in all_classes.json()
                        if item["id"] != new_class.json()["id"]
                    )
                    teacher_invitation = client.post(
                        "/api/v1/teachers/invitations",
                        headers=student_headers,
                        json={
                            "email": "teacher@example.com",
                            "class_section_ids": [new_class.json()["id"]],
                        },
                    )
                    self.assertEqual(
                        teacher_invitation.status_code,
                        202,
                        teacher_invitation.text,
                    )
                    teacher_invitation_link = re.search(
                        r"https?://[^\s]+",
                        send_email.await_args.args[2],
                    )
                    self.assertIsNotNone(teacher_invitation_link)
                    teacher_invitation_token = parse_qs(
                        urlsplit(teacher_invitation_link.group(0)).query
                    )["token"][0]
                    teacher_acceptance = client.post(
                        "/api/v1/auth/invitations/accept",
                        headers=greenwood_headers,
                        json={
                            "token": teacher_invitation_token,
                            "password": "teacher-integration-password",
                        },
                    )
                    self.assertEqual(teacher_acceptance.status_code, 200)
                    teacher_login = client.post(
                        "/api/v1/auth/login",
                        headers=greenwood_headers,
                        json={
                            "email": "teacher@example.com",
                            "password": "teacher-integration-password",
                        },
                    )
                    self.assertEqual(teacher_login.status_code, 200)
                    self.assertEqual(teacher_login.json()["role"], "teacher")
                    teacher_headers = {
                        **greenwood_headers,
                        "Authorization": f"Bearer {teacher_login.json()['access_token']}",
                    }
                    teacher_account = client.get(
                        "/api/v1/auth/me",
                        headers=teacher_headers,
                    )
                    teacher_id = teacher_account.json()["id"]
                    staff_profile = client.post(
                        "/api/v1/staff",
                        headers=student_headers,
                        json={
                            "staff_number": "STF-001",
                            "first_name": "Mina",
                            "last_name": "Okafor",
                            "job_title": "Librarian",
                            "hire_date": "2024-09-01",
                        },
                    )
                    self.assertEqual(staff_profile.status_code, 201, staff_profile.text)
                    profile_id = staff_profile.json()["id"]
                    self.assertEqual(staff_profile.json()["staff_number"], "STF-001")
                    self.assertEqual(
                        client.post(
                            "/api/v1/staff",
                            headers=student_headers,
                            json={
                                "staff_number": "STF-001",
                                "first_name": "Another",
                                "last_name": "Staff",
                                "job_title": "Assistant",
                            },
                        ).status_code,
                        409,
                    )
                    profile_update = client.patch(
                        f"/api/v1/staff/{profile_id}",
                        headers=student_headers,
                        json={"job_title": "Senior Librarian", "status": "inactive"},
                    )
                    self.assertEqual(profile_update.status_code, 200, profile_update.text)
                    self.assertEqual(profile_update.json()["job_title"], "Senior Librarian")
                    self.assertEqual(profile_update.json()["status"], "inactive")
                    invalid_profile_update = client.patch(
                        f"/api/v1/staff/{profile_id}",
                        headers=student_headers,
                        json={"job_title": None},
                    )
                    self.assertEqual(invalid_profile_update.status_code, 422)

                    teacher_profile = client.post(
                        "/api/v1/staff",
                        headers=student_headers,
                        json={
                            "staff_number": "TEA-001",
                            "first_name": "Teacher",
                            "last_name": "Example",
                            "job_title": "Teacher",
                            "linked_user_id": teacher_id,
                            "email": "teacher@example.com",
                        },
                    )
                    self.assertEqual(teacher_profile.status_code, 201, teacher_profile.text)
                    self.assertEqual(teacher_profile.json()["linked_user_id"], teacher_id)
                    self.assertEqual(
                        client.post(
                            "/api/v1/staff",
                            headers=student_headers,
                            json={
                                "staff_number": "TEA-002",
                                "first_name": "Teacher",
                                "last_name": "Duplicate",
                                "job_title": "Teacher",
                                "linked_user_id": teacher_id,
                            },
                        ).status_code,
                        409,
                    )

                    qualification = client.post(
                        f"/api/v1/staff/{profile_id}/qualifications",
                        headers=student_headers,
                        json={
                            "title": "Library Science Diploma",
                            "institution": "Greenwood College",
                            "field_of_study": "Library studies",
                            "awarded_on": "2020-06-15",
                        },
                    )
                    self.assertEqual(qualification.status_code, 201, qualification.text)
                    contract = client.post(
                        f"/api/v1/staff/{profile_id}/contracts",
                        headers=student_headers,
                        json={
                            "contract_type": "Permanent",
                            "start_date": "2024-09-01",
                            "end_date": "2027-08-31",
                            "status": "active",
                            "reference": "HR-2024-01",
                        },
                    )
                    self.assertEqual(contract.status_code, 201, contract.text)
                    contract_update = client.patch(
                        f"/api/v1/staff/{profile_id}/contracts/{contract.json()['id']}",
                        headers=student_headers,
                        json={"status": "ended"},
                    )
                    self.assertEqual(contract_update.status_code, 200, contract_update.text)
                    self.assertEqual(contract_update.json()["status"], "ended")
                    staff_directory = client.get("/api/v1/staff", headers=student_headers)
                    self.assertEqual(staff_directory.status_code, 200, staff_directory.text)
                    staff_record = next(
                        profile for profile in staff_directory.json()
                        if profile["id"] == profile_id
                    )
                    self.assertEqual(len(staff_record["qualifications"]), 1)
                    self.assertEqual(staff_record["contracts"][0]["status"], "ended")
                    invalid_contract = client.post(
                        f"/api/v1/staff/{profile_id}/contracts",
                        headers=student_headers,
                        json={
                            "contract_type": "Invalid dates",
                            "start_date": "2025-01-01",
                            "end_date": "2024-01-01",
                        },
                    )
                    self.assertEqual(invalid_contract.status_code, 422)
                    teacher_staff_access = client.get("/api/v1/staff", headers=teacher_headers)
                    self.assertEqual(teacher_staff_access.status_code, 403)
                    cross_school_staff_access = client.get(
                        "/api/v1/staff",
                        headers={
                            **oakwood_headers,
                            "Authorization": student_headers["Authorization"],
                        },
                    )
                    self.assertEqual(cross_school_staff_access.status_code, 403)

                    teacher_classes = client.get(
                        "/api/v1/teacher/classes",
                        headers=teacher_headers,
                    )
                    self.assertEqual(
                        [item["id"] for item in teacher_classes.json()],
                        [new_class.json()["id"]],
                    )
                    student_id = admitted_student.json()["id"]
                    parent_teachers = client.get(
                        f"/api/v1/parents/children/{student_id}/teachers",
                        headers=guardian_headers,
                    )
                    self.assertEqual(parent_teachers.status_code, 200, parent_teachers.text)
                    self.assertEqual(parent_teachers.json()[0]["teacher_id"], teacher_id)
                    created_thread = client.post(
                        "/api/v1/messages/threads",
                        headers=guardian_headers,
                        json={"student_id": student_id, "teacher_id": teacher_id},
                    )
                    self.assertEqual(created_thread.status_code, 201, created_thread.text)
                    thread_id = created_thread.json()["id"]
                    self.assertEqual(created_thread.json()["student_name"], "Amina Okafor")
                    self.assertEqual(
                        client.post(
                            "/api/v1/messages/threads",
                            headers=guardian_headers,
                            json={"student_id": student_id, "teacher_id": teacher_id},
                        ).json()["id"],
                        thread_id,
                    )
                    guardian_threads = client.get(
                        "/api/v1/messages/threads",
                        headers=guardian_headers,
                    )
                    self.assertEqual(guardian_threads.status_code, 200, guardian_threads.text)
                    self.assertEqual(guardian_threads.json()["total"], 1)
                    teacher_threads = client.get(
                        "/api/v1/messages/threads",
                        headers=teacher_headers,
                    )
                    self.assertEqual(teacher_threads.status_code, 200, teacher_threads.text)
                    self.assertEqual(teacher_threads.json()["total"], 1)
                    teacher_message = client.post(
                        f"/api/v1/messages/threads/{thread_id}/messages",
                        headers=teacher_headers,
                        json={"body": "Amina did excellent work in class today."},
                    )
                    self.assertEqual(teacher_message.status_code, 201, teacher_message.text)
                    parent_thread = client.get(
                        f"/api/v1/messages/threads/{thread_id}",
                        headers=guardian_headers,
                    )
                    self.assertEqual(parent_thread.status_code, 200, parent_thread.text)
                    self.assertEqual(parent_thread.json()["message_count"], 1)
                    self.assertTrue(parent_thread.json()["messages"][0]["can_flag"])
                    message_flag = client.post(
                        f"/api/v1/messages/{teacher_message.json()['id']}/flags",
                        headers=guardian_headers,
                        json={"reason": "This message needs administrator review."},
                    )
                    self.assertEqual(message_flag.status_code, 201, message_flag.text)
                    self.assertEqual(message_flag.json()["status"], "open")
                    moderation_queue = client.get(
                        "/api/v1/messages/moderation/flags",
                        headers=student_headers,
                    )
                    self.assertEqual(moderation_queue.status_code, 200, moderation_queue.text)
                    self.assertEqual(moderation_queue.json()["total"], 1)
                    self.assertEqual(moderation_queue.json()["items"][0]["student_name"], "Amina Okafor")
                    self.assertEqual(
                        client.get(
                            "/api/v1/messages/moderation/flags",
                            headers=teacher_headers,
                        ).status_code,
                        403,
                    )
                    reviewed_flag = client.patch(
                        f"/api/v1/messages/moderation/flags/{message_flag.json()['id']}",
                        headers=student_headers,
                        json={"status": "reviewed"},
                    )
                    self.assertEqual(reviewed_flag.status_code, 200, reviewed_flag.text)
                    self.assertEqual(reviewed_flag.json()["status"], "reviewed")
                    self.assertEqual(
                        client.get(
                            "/api/v1/messages/moderation/flags",
                            headers=student_headers,
                        ).json()["total"],
                        0,
                    )
                    self.assertEqual(
                        client.get(
                            f"/api/v1/messages/threads/{thread_id}",
                            headers={
                                **oakwood_headers,
                                "Authorization": guardian_headers["Authorization"],
                            },
                        ).status_code,
                        403,
                    )
                    teacher_roster = client.get(
                        f"/api/v1/classes/{new_class.json()['id']}/attendance-roster",
                        headers=teacher_headers,
                        params={"attendance_date": attendance_date},
                    )
                    self.assertEqual(teacher_roster.status_code, 200)
                    unassigned_roster = client.get(
                        f"/api/v1/classes/{unassigned_class_id}/attendance-roster",
                        headers=teacher_headers,
                        params={"attendance_date": attendance_date},
                    )
                    self.assertEqual(unassigned_roster.status_code, 403)
                    teacher_assignments = client.put(
                        f"/api/v1/teachers/{teacher_id}/classes",
                        headers=student_headers,
                        json={"class_section_ids": []},
                    )
                    self.assertEqual(teacher_assignments.status_code, 200)
                    removed_class_access = client.get(
                        f"/api/v1/classes/{new_class.json()['id']}/attendance-roster",
                        headers=teacher_headers,
                        params={"attendance_date": attendance_date},
                    )
                    self.assertEqual(removed_class_access.status_code, 403)
                    self.assertEqual(
                        client.get(
                            f"/api/v1/messages/threads/{thread_id}",
                            headers=teacher_headers,
                        ).status_code,
                        404,
                    )
                    teacher_assignments = client.put(
                        f"/api/v1/teachers/{teacher_id}/classes",
                        headers=student_headers,
                        json={"class_section_ids": [new_class.json()["id"]]},
                    )
                    self.assertEqual(teacher_assignments.status_code, 200)
                    timetable_payload = {
                        "class_section_id": new_class.json()["id"],
                        "teacher_id": teacher_id,
                        "subject": "Mathematics",
                        "room": "Room 12",
                        "weekday": 0,
                        "start_time": "09:00",
                        "end_time": "10:00",
                    }
                    self.assertEqual(
                        client.get("/api/v1/timetable", headers=teacher_headers).status_code,
                        403,
                    )
                    created_lesson = client.post(
                        "/api/v1/timetable",
                        headers=student_headers,
                        json=timetable_payload,
                    )
                    self.assertEqual(created_lesson.status_code, 201, created_lesson.text)
                    self.assertEqual(created_lesson.json()["class_name"], "Grade 5B")
                    self.assertEqual(created_lesson.json()["teacher_email"], "teacher@example.com")
                    invalid_lesson_time = client.post(
                        "/api/v1/timetable",
                        headers=student_headers,
                        json={**timetable_payload, "start_time": "11:00", "end_time": "10:00"},
                    )
                    self.assertEqual(invalid_lesson_time.status_code, 422)
                    duplicate_lesson = client.post(
                        "/api/v1/timetable",
                        headers=student_headers,
                        json={**timetable_payload, "subject": "English", "start_time": "09:30"},
                    )
                    self.assertEqual(duplicate_lesson.status_code, 409)
                    teacher_timetable = client.get(
                        "/api/v1/teacher/timetable",
                        headers=teacher_headers,
                    )
                    self.assertEqual(teacher_timetable.status_code, 200, teacher_timetable.text)
                    self.assertEqual(len(teacher_timetable.json()), 1)
                    self.assertEqual(teacher_timetable.json()[0]["subject"], "Mathematics")
                    teacher_assignments = client.put(
                        f"/api/v1/teachers/{teacher_id}/classes",
                        headers=student_headers,
                        json={"class_section_ids": [new_class.json()["id"], unassigned_class_id]},
                    )
                    self.assertEqual(teacher_assignments.status_code, 200)
                    conflicting_teacher_lesson = client.post(
                        "/api/v1/timetable",
                        headers=student_headers,
                        json={
                            **timetable_payload,
                            "class_section_id": unassigned_class_id,
                            "room": "Room 15",
                            "subject": "Science",
                        },
                    )
                    self.assertEqual(conflicting_teacher_lesson.status_code, 409)
                    teacher_assignments = client.put(
                        f"/api/v1/teachers/{teacher_id}/classes",
                        headers=student_headers,
                        json={"class_section_ids": [new_class.json()["id"]]},
                    )
                    self.assertEqual(teacher_assignments.status_code, 200)
                    updated_lesson = client.put(
                        f"/api/v1/timetable/{created_lesson.json()['id']}",
                        headers=student_headers,
                        json={**timetable_payload, "start_time": "10:00", "end_time": "11:00"},
                    )
                    self.assertEqual(updated_lesson.status_code, 200, updated_lesson.text)
                    self.assertEqual(updated_lesson.json()["start_time"], "10:00")
                    deleted_lesson = client.delete(
                        f"/api/v1/timetable/{created_lesson.json()['id']}",
                        headers=student_headers,
                    )
                    self.assertEqual(deleted_lesson.status_code, 204)
                    self.assertEqual(
                        client.delete(
                            f"/api/v1/timetable/{created_lesson.json()['id']}",
                            headers=student_headers,
                        ).status_code,
                        404,
                    )
                    updated_student = client.get(
                        f"/api/v1/students/{admitted_student.json()['id']}",
                        headers=student_headers,
                    )
                    self.assertEqual(updated_student.json()["class_name"], "Grade 5B")

                    roster_response = client.get(
                        f"/api/v1/classes/{new_class.json()['id']}/attendance-roster",
                        headers=student_headers,
                        params={"attendance_date": attendance_date},
                    )
                    self.assertEqual(roster_response.status_code, 200, roster_response.text)
                    self.assertEqual(
                        [row["student_id"] for row in roster_response.json()["students"]],
                        [admitted_student.json()["id"]],
                    )

                    attendance_entry = {
                        "local_id": "c1b54999-dfa0-42f3-aa1a-e03ab6d3ad45",
                        "student_id": admitted_student.json()["id"],
                        "class_section_id": new_class.json()["id"],
                        "attendance_date": attendance_date,
                        "status": "present",
                        "client_timestamp": datetime.now(UTC).isoformat(),
                    }
                    attendance_sync = client.post(
                        "/api/v1/attendance/sync",
                        headers=student_headers,
                        json={"entries": [attendance_entry]},
                    )
                    self.assertEqual(attendance_sync.status_code, 200, attendance_sync.text)
                    self.assertEqual(
                        attendance_sync.json()["results"][0]["status"],
                        "synced",
                    )
                    duplicate_sync = client.post(
                        "/api/v1/attendance/sync",
                        headers=student_headers,
                        json={"entries": [attendance_entry]},
                    )
                    self.assertEqual(duplicate_sync.status_code, 200)
                    self.assertEqual(
                        duplicate_sync.json()["results"][0]["status"],
                        "duplicate",
                    )

                    updated_attendance_entry = {
                        **attendance_entry,
                        "local_id": "24e77d5e-2b64-4499-a2e4-8e954bec892b",
                        "status": "late",
                        "client_timestamp": (
                            datetime.now(UTC) + timedelta(seconds=2)
                        ).isoformat(),
                    }
                    updated_attendance = client.post(
                        "/api/v1/attendance/sync",
                        headers=student_headers,
                        json={"entries": [updated_attendance_entry]},
                    )
                    self.assertEqual(updated_attendance.status_code, 200)
                    self.assertEqual(
                        updated_attendance.json()["results"][0]["status"],
                        "synced",
                    )
                    stale_attendance = client.post(
                        "/api/v1/attendance/sync",
                        headers=student_headers,
                        json={"entries": [attendance_entry]},
                    )
                    self.assertEqual(
                        stale_attendance.json()["results"][0]["status"],
                        "duplicate",
                    )
                    refreshed_roster = client.get(
                        f"/api/v1/classes/{new_class.json()['id']}/attendance-roster",
                        headers=student_headers,
                        params={"attendance_date": attendance_date},
                    )
                    self.assertEqual(
                        refreshed_roster.json()["students"][0]["attendance_status"],
                        "late",
                    )

                    academic_year_id = new_class.json()["academic_year_id"]
                    created_term = client.post(
                        f"/api/v1/academic-years/{academic_year_id}/terms",
                        headers=student_headers,
                        json={
                            "name": "Term 1",
                            "start_date": attendance_date,
                            "end_date": (attendance_day + timedelta(days=30)).isoformat(),
                        },
                    )
                    self.assertEqual(created_term.status_code, 201, created_term.text)
                    default_billing_settings = client.get(
                        "/api/v1/billing/settings",
                        headers=student_headers,
                    )
                    self.assertEqual(default_billing_settings.status_code, 200)
                    self.assertEqual(default_billing_settings.json()["provider"], "manual")
                    self.assertEqual(default_billing_settings.json()["currency"], "NGN")

                    configured_billing_settings = client.put(
                        "/api/v1/billing/settings",
                        headers=student_headers,
                        json={"provider": "manual", "currency": "USD"},
                    )
                    self.assertEqual(configured_billing_settings.status_code, 200)
                    self.assertEqual(configured_billing_settings.json()["currency"], "USD")

                    created_fee = client.post(
                        "/api/v1/billing/fees",
                        headers=student_headers,
                        json={
                            "class_section_id": new_class.json()["id"],
                            "term_id": created_term.json()["id"],
                            "description": "Tuition",
                            "amount": "125.50",
                        },
                    )
                    self.assertEqual(created_fee.status_code, 201, created_fee.text)
                    self.assertEqual(created_fee.json()["currency"], "USD")

                    invoice_generation = client.post(
                        "/api/v1/billing/invoices/generate",
                        headers=student_headers,
                        json={
                            "class_section_id": new_class.json()["id"],
                            "term_id": created_term.json()["id"],
                            "due_date": (attendance_day + timedelta(days=45)).isoformat(),
                        },
                    )
                    self.assertEqual(invoice_generation.status_code, 201, invoice_generation.text)
                    self.assertEqual(invoice_generation.json()["created"], 1)
                    invoice = invoice_generation.json()["invoices"][0]
                    self.assertEqual(invoice["amount"], "125.50")
                    self.assertEqual(invoice["currency"], "USD")
                    self.assertEqual(invoice["balance"], "125.50")
                    billing_dashboard = client.get(
                        "/api/v1/billing/dashboard",
                        headers=student_headers,
                    )
                    self.assertEqual(billing_dashboard.status_code, 200, billing_dashboard.text)
                    self.assertEqual(
                        billing_dashboard.json()["summaries"],
                        [{
                            "currency": "USD",
                            "total_invoiced": "125.50",
                            "total_collected": "0.00",
                            "total_outstanding": "125.50",
                            "collection_rate": "0",
                            "invoice_count": 1,
                            "paid_invoice_count": 0,
                            "overdue_invoice_count": 0,
                        }],
                    )
                    self.assertEqual(billing_dashboard.json()["defaulters_total"], 0)
                    guardian_billing_dashboard = client.get(
                        "/api/v1/billing/dashboard",
                        headers=guardian_headers,
                    )
                    self.assertEqual(guardian_billing_dashboard.status_code, 403)
                    invalid_dashboard_limit = client.get(
                        "/api/v1/billing/dashboard?defaulters_limit=101",
                        headers=student_headers,
                    )
                    self.assertEqual(invalid_dashboard_limit.status_code, 422)
                    repeated_generation = client.post(
                        "/api/v1/billing/invoices/generate",
                        headers=student_headers,
                        json={
                            "class_section_id": new_class.json()["id"],
                            "term_id": created_term.json()["id"],
                            "due_date": (attendance_day + timedelta(days=45)).isoformat(),
                        },
                    )
                    self.assertEqual(repeated_generation.status_code, 201)
                    self.assertEqual(repeated_generation.json()["created"], 0)
                    self.assertEqual(repeated_generation.json()["skipped"], 1)

                    overpayment = client.post(
                        f"/api/v1/billing/invoices/{invoice['id']}/payments",
                        headers=student_headers,
                        json={"amount": "125.51", "method": "cash"},
                    )
                    self.assertEqual(overpayment.status_code, 409)
                    payment_response = client.post(
                        f"/api/v1/billing/invoices/{invoice['id']}/payments",
                        headers=student_headers,
                        json={"amount": "125.50", "method": "cash"},
                    )
                    self.assertEqual(payment_response.status_code, 201, payment_response.text)
                    self.assertEqual(payment_response.json()["balance"], "0.00")
                    self.assertEqual(payment_response.json()["status"], "paid")
                    paid_dashboard = client.get(
                        "/api/v1/billing/dashboard",
                        headers=student_headers,
                        params={"term_id": created_term.json()["id"]},
                    )
                    self.assertEqual(paid_dashboard.status_code, 200, paid_dashboard.text)
                    self.assertEqual(paid_dashboard.json()["summaries"][0]["total_collected"], "125.50")
                    self.assertEqual(paid_dashboard.json()["summaries"][0]["total_outstanding"], "0.00")
                    self.assertEqual(paid_dashboard.json()["summaries"][0]["collection_rate"], "100")
                    parent_invoices = client.get(
                        "/api/v1/parents/invoices",
                        headers=guardian_headers,
                    )
                    self.assertEqual(parent_invoices.status_code, 200, parent_invoices.text)
                    self.assertEqual(parent_invoices.json()["items"][0]["id"], invoice["id"])
                    self.assertEqual(len(parent_invoices.json()["items"][0]["payments"]), 1)

                    voided_payment = client.post(
                        f"/api/v1/billing/payments/{payment_response.json()['payments'][0]['id']}/void",
                        headers=student_headers,
                        json={"reason": "Payment entered in error"},
                    )
                    self.assertEqual(voided_payment.status_code, 200, voided_payment.text)
                    self.assertEqual(voided_payment.json()["balance"], "125.50")
                    self.assertEqual(voided_payment.json()["status"], "issued")
                    voided_dashboard = client.get(
                        "/api/v1/billing/dashboard",
                        headers=student_headers,
                    )
                    self.assertEqual(voided_dashboard.status_code, 200, voided_dashboard.text)
                    self.assertEqual(voided_dashboard.json()["summaries"][0]["total_collected"], "0.00")
                    self.assertEqual(voided_dashboard.json()["summaries"][0]["total_outstanding"], "125.50")

                    gateway_settings = client.put(
                        "/api/v1/billing/settings",
                        headers=student_headers,
                        json={
                            "provider": "stripe",
                            "currency": "GBP",
                            "secret_key": "sk_test_never_return_this",
                            "webhook_secret": "whsec_never_return_this",
                        },
                    )
                    self.assertEqual(gateway_settings.status_code, 200, gateway_settings.text)
                    self.assertTrue(gateway_settings.json()["configured"])
                    self.assertNotIn("sk_test_never_return_this", gateway_settings.text)
                    self.assertNotIn("whsec_never_return_this", gateway_settings.text)
                    self.assertEqual(invoice["currency"], "USD")

                    checkout_http_client = AsyncMock()
                    checkout_response = MagicMock()
                    checkout_response.json.return_value = {
                        "id": "cs_test_checkout",
                        "url": "https://checkout.stripe.com/c/pay/cs_test_checkout",
                    }
                    checkout_response.raise_for_status.return_value = None
                    checkout_http_client.post.return_value = checkout_response
                    checkout_context = AsyncMock()
                    checkout_context.__aenter__.return_value = checkout_http_client
                    checkout_context.__aexit__.return_value = None
                    with patch("app.main.httpx.AsyncClient", return_value=checkout_context):
                        checkout = client.post(
                            f"/api/v1/parents/invoices/{invoice['id']}/checkout",
                            headers=guardian_headers,
                        )
                    self.assertEqual(checkout.status_code, 200, checkout.text)
                    self.assertEqual(checkout.json()["provider"], "stripe")
                    self.assertEqual(
                        checkout.json()["checkout_url"],
                        "https://checkout.stripe.com/c/pay/cs_test_checkout",
                    )

                    stripe_event_body = json.dumps(
                        {
                            "type": "checkout.session.completed",
                            "data": {
                                "object": {
                                    "id": "cs_test_checkout",
                                    "payment_status": "paid",
                                    "amount_total": 12550,
                                    "currency": "usd",
                                    "metadata": {"invoice_id": invoice["id"]},
                                }
                            },
                        },
                        separators=(",", ":"),
                    ).encode()
                    stripe_timestamp = str(int(time.time()))
                    stripe_signature = hmac.new(
                        b"whsec_never_return_this",
                        stripe_timestamp.encode() + b"." + stripe_event_body,
                        hashlib.sha256,
                    ).hexdigest()
                    invalid_webhook = client.post(
                        "/api/v1/billing/webhooks/stripe",
                        headers={
                            **greenwood_headers,
                            "Stripe-Signature": f"t={stripe_timestamp},v1=invalid",
                            "Content-Type": "application/json",
                        },
                        content=stripe_event_body,
                    )
                    self.assertEqual(invalid_webhook.status_code, 401)
                    webhook = client.post(
                        "/api/v1/billing/webhooks/stripe",
                        headers={
                            **greenwood_headers,
                            "Stripe-Signature": f"t={stripe_timestamp},v1={stripe_signature}",
                            "Content-Type": "application/json",
                        },
                        content=stripe_event_body,
                    )
                    self.assertEqual(webhook.status_code, 200, webhook.text)
                    self.assertTrue(webhook.json()["applied"])
                    repeated_webhook = client.post(
                        "/api/v1/billing/webhooks/stripe",
                        headers={
                            **greenwood_headers,
                            "Stripe-Signature": f"t={stripe_timestamp},v1={stripe_signature}",
                            "Content-Type": "application/json",
                        },
                        content=stripe_event_body,
                    )
                    self.assertEqual(repeated_webhook.status_code, 200)
                    self.assertTrue(repeated_webhook.json()["applied"])
                    paid_invoice_list = client.get(
                        "/api/v1/billing/invoices",
                        headers=student_headers,
                    )
                    self.assertEqual(paid_invoice_list.status_code, 200)
                    self.assertEqual(paid_invoice_list.json()["items"][0]["status"], "paid")

                    overlapping_term = client.post(
                        f"/api/v1/academic-years/{academic_year_id}/terms",
                        headers=student_headers,
                        json={
                            "name": "Overlapping term",
                            "start_date": attendance_date,
                            "end_date": (attendance_day + timedelta(days=10)).isoformat(),
                        },
                    )
                    self.assertEqual(overlapping_term.status_code, 409)

                    assessment_payload = {
                        "term_id": created_term.json()["id"],
                        "subject": "Mathematics",
                        "title": "Mid-term test",
                        "assessment_date": attendance_date,
                        "max_score": 100,
                    }
                    created_assessment = client.post(
                        f"/api/v1/classes/{new_class.json()['id']}/assessments",
                        headers=student_headers,
                        json=assessment_payload,
                    )
                    self.assertEqual(
                        created_assessment.status_code,
                        201,
                        created_assessment.text,
                    )
                    assessment_id = created_assessment.json()["id"]
                    term_assessments = client.get(
                        f"/api/v1/classes/{new_class.json()['id']}/assessments",
                        headers=student_headers,
                        params={"term_id": created_term.json()["id"]},
                    )
                    self.assertEqual(term_assessments.status_code, 200)
                    self.assertEqual(term_assessments.json()[0]["term_name"], "Term 1")
                    unassigned_assessments = client.get(
                        f"/api/v1/classes/{new_class.json()['id']}/assessments",
                        headers=student_headers,
                        params={"term_id": "unassigned"},
                    )
                    self.assertEqual(unassigned_assessments.status_code, 200)
                    self.assertEqual(unassigned_assessments.json(), [])
                    teacher_assessment_list = client.get(
                        f"/api/v1/classes/{new_class.json()['id']}/assessments",
                        headers=teacher_headers,
                    )
                    self.assertEqual(teacher_assessment_list.status_code, 200)
                    teacher_gradebook = client.get(
                        f"/api/v1/assessments/{assessment_id}/grades",
                        headers=teacher_headers,
                    )
                    self.assertEqual(teacher_gradebook.status_code, 200)
                    unassigned_assessment_list = client.get(
                        f"/api/v1/classes/{unassigned_class_id}/assessments",
                        headers=teacher_headers,
                    )
                    self.assertEqual(unassigned_assessment_list.status_code, 403)
                    duplicate_assessment = client.post(
                        f"/api/v1/classes/{new_class.json()['id']}/assessments",
                        headers=student_headers,
                        json=assessment_payload,
                    )
                    self.assertEqual(duplicate_assessment.status_code, 409)

                    gradebook = client.get(
                        f"/api/v1/assessments/{assessment_id}/grades",
                        headers=student_headers,
                    )
                    self.assertEqual(gradebook.status_code, 200, gradebook.text)
                    self.assertEqual(len(gradebook.json()["students"]), 1)
                    student_id = admitted_student.json()["id"]
                    invalid_grade = client.put(
                        f"/api/v1/assessments/{assessment_id}/grades",
                        headers=student_headers,
                        json={"entries": [{"student_id": student_id, "score": 101}]},
                    )
                    self.assertEqual(invalid_grade.status_code, 422)

                    saved_grade = client.put(
                        f"/api/v1/assessments/{assessment_id}/grades",
                        headers=teacher_headers,
                        json={"entries": [{"student_id": student_id, "score": 87.5}]},
                    )
                    self.assertEqual(saved_grade.status_code, 200, saved_grade.text)
                    self.assertEqual(saved_grade.json()["updated_count"], 1)
                    analytics = client.get(
                        "/api/v1/analytics/dashboard",
                        headers=student_headers,
                    )
                    self.assertEqual(analytics.status_code, 200, analytics.text)
                    analytics_data = analytics.json()
                    self.assertGreaterEqual(analytics_data["active_students"], 1)
                    self.assertGreaterEqual(analytics_data["active_enrollments"], 1)
                    self.assertEqual(len(analytics_data["enrollment_trend"]), 12)
                    self.assertGreaterEqual(analytics_data["attendance"]["marked_count"], 1)
                    self.assertEqual(float(analytics_data["attendance"]["rate"]), 100.0)
                    self.assertEqual(analytics_data["fee_summaries"][0]["currency"], "USD")
                    self.assertEqual(
                        float(analytics_data["fee_summaries"][0]["collection_rate"]),
                        100.0,
                    )
                    self.assertEqual(float(analytics_data["average_grade"]), 87.5)
                    self.assertEqual(analytics_data["graded_count"], 1)
                    self.assertEqual(
                        next(
                            band for band in analytics_data["grade_distribution"]
                            if band["band"] == "80-100"
                        )["count"],
                        1,
                    )
                    self.assertEqual(
                        client.get("/api/v1/analytics/dashboard", headers=guardian_headers).status_code,
                        403,
                    )
                    self.assertEqual(
                        client.get("/api/v1/analytics/dashboard", headers=teacher_headers).status_code,
                        403,
                    )
                    cross_school_analytics = client.get(
                        "/api/v1/analytics/dashboard",
                        headers={
                            **oakwood_headers,
                            "Authorization": student_headers["Authorization"],
                        },
                    )
                    self.assertEqual(cross_school_analytics.status_code, 403)
                    gradebook = client.get(
                        f"/api/v1/assessments/{assessment_id}/grades",
                        headers=student_headers,
                    )
                    self.assertEqual(
                        float(gradebook.json()["students"][0]["score"]),
                        87.5,
                    )
                    report_cards = client.get(
                        f"/api/v1/classes/{new_class.json()['id']}/terms/{created_term.json()['id']}/report-cards",
                        headers=teacher_headers,
                    )
                    self.assertEqual(report_cards.status_code, 200, report_cards.text)
                    report_card = report_cards.json()[0]
                    self.assertEqual(report_card["term_name"], "Term 1")
                    self.assertEqual(float(report_card["overall_percentage"]), 87.5)
                    self.assertEqual(report_card["attendance"]["late"], 1)
                    guardian_unfiltered_report = client.get(
                        f"/api/v1/classes/{new_class.json()['id']}/terms/{created_term.json()['id']}/report-cards",
                        headers=guardian_headers,
                    )
                    self.assertEqual(guardian_unfiltered_report.status_code, 403)
                    guardian_filtered_report = client.get(
                        f"/api/v1/classes/{new_class.json()['id']}/terms/{created_term.json()['id']}/report-cards",
                        headers=guardian_headers,
                        params={"student_id": student_id},
                    )
                    self.assertEqual(guardian_filtered_report.status_code, 200)
                    self.assertEqual(guardian_filtered_report.json(), [])
                    parent_report_cards = client.get(
                        f"/api/v1/parents/children/{student_id}/report-cards",
                        headers=guardian_headers,
                    )
                    self.assertEqual(
                        parent_report_cards.status_code,
                        200,
                        parent_report_cards.text,
                    )
                    self.assertEqual(parent_report_cards.json(), [])

                    send_email.side_effect = smtplib.SMTPException("SMTP delivery failed")
                    publish_report_cards = client.post(
                        f"/api/v1/classes/{new_class.json()['id']}/terms/{created_term.json()['id']}/report-cards/publish",
                        headers=student_headers,
                    )
                    self.assertEqual(
                        publish_report_cards.status_code,
                        200,
                        publish_report_cards.text,
                    )
                    publication = publish_report_cards.json()
                    self.assertFalse(publication["already_published"])
                    self.assertEqual(publication["notifications_sent"], 0)
                    self.assertEqual(publication["notifications_failed"], 1)

                    send_email.side_effect = None
                    retry_report_card_notifications = client.post(
                        f"/api/v1/classes/{new_class.json()['id']}/terms/{created_term.json()['id']}/report-cards/publish",
                        headers=student_headers,
                    )
                    self.assertEqual(retry_report_card_notifications.status_code, 200)
                    self.assertTrue(retry_report_card_notifications.json()["already_published"])
                    self.assertEqual(retry_report_card_notifications.json()["notifications_sent"], 1)
                    self.assertEqual(retry_report_card_notifications.json()["notifications_failed"], 0)
                    self.assertEqual(send_email.await_args.args[0], "ada@example.com")
                    self.assertIn("/parent", send_email.await_args.args[2])
                    send_count_after_publish = send_email.await_count
                    retry_report_card_notifications = client.post(
                        f"/api/v1/classes/{new_class.json()['id']}/terms/{created_term.json()['id']}/report-cards/publish",
                        headers=student_headers,
                    )
                    self.assertEqual(retry_report_card_notifications.status_code, 200)
                    self.assertEqual(send_email.await_count, send_count_after_publish)
                    self.assertEqual(
                        client.post(
                            f"/api/v1/classes/{new_class.json()['id']}/terms/{created_term.json()['id']}/report-cards/publish",
                            headers=teacher_headers,
                        ).status_code,
                        403,
                    )

                    parent_report_cards = client.get(
                        f"/api/v1/parents/children/{student_id}/report-cards",
                        headers=guardian_headers,
                    )
                    self.assertEqual(parent_report_cards.status_code, 200)
                    self.assertEqual(len(parent_report_cards.json()), 1)
                    self.assertIsNotNone(parent_report_cards.json()[0]["published_at"])
                    self.assertEqual(
                        float(parent_report_cards.json()[0]["overall_percentage"]),
                        87.5,
                    )
                    parent_attendance = client.get(
                        f"/api/v1/parents/children/{student_id}/attendance",
                        headers=guardian_headers,
                    )
                    self.assertEqual(parent_attendance.status_code, 200, parent_attendance.text)
                    self.assertEqual(parent_attendance.json()[0]["status"], "late")
                    another_child_report = client.get(
                        "/api/v1/parents/children/00000000000000000000000000000000/report-cards",
                        headers=guardian_headers,
                    )
                    self.assertEqual(another_child_report.status_code, 404)

                    cleared_grade = client.put(
                        f"/api/v1/assessments/{assessment_id}/grades",
                        headers=student_headers,
                        json={"entries": [{"student_id": student_id, "score": None}]},
                    )
                    self.assertEqual(cleared_grade.status_code, 200)
                    self.assertEqual(cleared_grade.json()["removed_count"], 1)

                    cross_tenant_gradebook = client.get(
                        f"/api/v1/assessments/{assessment_id}/grades",
                        headers={
                            **oakwood_headers,
                            "Authorization": f"Bearer {token}",
                        },
                    )
                    self.assertEqual(cross_tenant_gradebook.status_code, 403)

                    duplicate_student = client.post(
                        "/api/v1/students",
                        headers=student_headers,
                        json={
                            "admission_number": "GR-001",
                            "first_name": "Another",
                            "last_name": "Student",
                            "date_of_birth": "2015-05-04",
                            "gender": "female",
                            "class_name": "Grade 5A",
                            "academic_year": "2026-2027",
                            "guardian_name": "Ada Okafor",
                            "guardian_phone": "+2348000000000",
                        },
                    )
                    self.assertEqual(duplicate_student.status_code, 409)

                    imported_students = client.post(
                        "/api/v1/students/import",
                        headers={**student_headers, "Content-Type": "text/csv"},
                        content=(
                            "admission_number,first_name,last_name,date_of_birth,gender,"
                            "class_name,academic_year,guardian_name,guardian_email,guardian_phone\n"
                            "GR-002,Chidi,Okafor,2014-02-11,male,Grade 5A,2026-2027,"
                            "Ada Okafor,,+2348000000000\n"
                        ),
                    )
                    self.assertEqual(imported_students.status_code, 201, imported_students.text)
                    self.assertEqual(imported_students.json()["imported"], 1)

                    student_list = client.get(
                        "/api/v1/students?search=okafor",
                        headers=student_headers,
                    )
                    self.assertEqual(student_list.status_code, 200, student_list.text)
                    self.assertEqual(student_list.json()["total"], 2)

                    archived_student = client.patch(
                        f"/api/v1/students/{admitted_student.json()['id']}",
                        headers=student_headers,
                        json={
                            "first_name": "Amina Grace",
                            "is_active": False,
                            "medical_notes": "Peanut allergy",
                        },
                    )
                    self.assertEqual(archived_student.status_code, 200, archived_student.text)
                    self.assertEqual(archived_student.json()["first_name"], "Amina Grace")
                    self.assertFalse(archived_student.json()["is_active"])
                    active_students = client.get(
                        "/api/v1/students",
                        headers=student_headers,
                    )
                    self.assertEqual(active_students.json()["total"], 1)
                    all_students = client.get(
                        "/api/v1/students?include_inactive=true",
                        headers=student_headers,
                    )
                    self.assertEqual(all_students.json()["total"], 2)
                    class_sections = client.get(
                        "/api/v1/classes",
                        headers=student_headers,
                    )
                    grade_5b = next(
                        class_section
                        for class_section in class_sections.json()
                        if class_section["name"] == "Grade 5B"
                    )
                    self.assertEqual(grade_5b["student_count"], 0)
                    edited_class = client.patch(
                        f"/api/v1/classes/{grade_5b['id']}",
                        headers=student_headers,
                        json={"name": "Grade 5B Updated"},
                    )
                    self.assertEqual(edited_class.status_code, 200, edited_class.text)
                    self.assertEqual(edited_class.json()["name"], "Grade 5B Updated")
                    archived_class = client.patch(
                        f"/api/v1/classes/{grade_5b['id']}/archive",
                        headers=student_headers,
                    )
                    self.assertEqual(archived_class.status_code, 204)
                    active_classes = client.get(
                        "/api/v1/classes",
                        headers=student_headers,
                    )
                    self.assertNotIn(
                        grade_5b["id"],
                        {item["id"] for item in active_classes.json()},
                    )
                    all_classes = client.get(
                        "/api/v1/classes?include_inactive=true",
                        headers=student_headers,
                    )
                    archived_class_record = next(
                        item for item in all_classes.json()
                        if item["id"] == grade_5b["id"]
                    )
                    self.assertFalse(archived_class_record["is_active"])
                    unused_class = client.post(
                        "/api/v1/classes",
                        headers=student_headers,
                        json={
                            "name": "Grade 5C",
                            "academic_year_id": academic_year.json()["id"],
                        },
                    )
                    self.assertEqual(unused_class.status_code, 201, unused_class.text)
                    deleted_class = client.delete(
                        f"/api/v1/classes/{unused_class.json()['id']}",
                        headers=student_headers,
                    )
                    self.assertEqual(deleted_class.status_code, 204)
                    protected_year_delete = client.delete(
                        f"/api/v1/academic-years/{academic_year.json()['id']}",
                        headers=student_headers,
                    )
                    self.assertEqual(protected_year_delete.status_code, 409)

                    cross_tenant_students = client.get(
                        "/api/v1/students",
                        headers={
                            **oakwood_headers,
                            "Authorization": f"Bearer {token}",
                        },
                    )
                    self.assertEqual(cross_tenant_students.status_code, 403)

                    other_school_login = client.post(
                        "/api/v1/auth/login",
                        headers=oakwood_headers,
                        json={
                            "email": "principal@example.com",
                            "password": "integration-test-password",
                        },
                    )
                    self.assertEqual(other_school_login.status_code, 401)

                    reset_response = client.post(
                        "/api/v1/auth/password-reset/request",
                        headers=greenwood_headers,
                        json={"email": "principal@example.com"},
                    )
                    self.assertEqual(reset_response.status_code, 202, reset_response.text)
                    reset_body = send_email.await_args.args[2]
                    reset_link = re.search(r"https?://[^\s]+", reset_body)
                    self.assertIsNotNone(reset_link)
                    reset_token = parse_qs(urlsplit(reset_link.group(0)).query)["token"][0]

                    password_reset = client.post(
                        "/api/v1/auth/password-reset/confirm",
                        headers=greenwood_headers,
                        json={
                            "token": reset_token,
                            "password": "new-integration-password",
                        },
                    )
                    self.assertEqual(password_reset.status_code, 200, password_reset.text)
                    replayed_reset = client.post(
                        "/api/v1/auth/password-reset/confirm",
                        headers=greenwood_headers,
                        json={
                            "token": reset_token,
                            "password": "another-integration-password",
                        },
                    )
                    self.assertEqual(replayed_reset.status_code, 400)
                    old_password_login = client.post(
                        "/api/v1/auth/login",
                        headers=greenwood_headers,
                        json={
                            "email": "principal@example.com",
                            "password": "integration-test-password",
                        },
                    )
                    self.assertEqual(old_password_login.status_code, 401)
                    new_password_login = client.post(
                        "/api/v1/auth/login",
                        headers=greenwood_headers,
                        json={
                            "email": "principal@example.com",
                            "password": "new-integration-password",
                        },
                    )
                    self.assertEqual(new_password_login.status_code, 200)

                    cross_school_access = client.get(
                        "/api/v1/auth/me",
                        headers={**oakwood_headers, "Authorization": f"Bearer {token}"},
                    )
                    self.assertEqual(cross_school_access.status_code, 403)
                    self.assertEqual(cross_school_access.json()["detail"], "Tenant mismatch")
            finally:
                mail_delivery.stop()
                environment.stop()


class ActionTokenExpiryTests(unittest.TestCase):
    def test_expired_action_token_cannot_be_consumed(self) -> None:
        async def verify_expired_token() -> tuple[str | None, datetime | None]:
            engine = create_async_engine("sqlite+aiosqlite:///:memory:")
            async with engine.begin() as connection:
                await connection.run_sync(TenantBase.metadata.create_all)

            token = "expired-action-token-value-that-is-long-enough"
            token_hash = make_token_hash(token)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as session:
                await session.execute(
                    insert(TenantUser.__table__).values(
                        id="expired-token-user",
                        email="expired@example.com",
                        password_hash=DUMMY_PASSWORD_HASH,
                        role="school_admin",
                        is_active=True,
                    )
                )
                await session.execute(
                    insert(UserActionToken.__table__).values(
                        token_hash=token_hash,
                        user_id="expired-token-user",
                        purpose="password_reset",
                        expires_at=datetime.now(UTC) - timedelta(seconds=1),
                        used_at=None,
                    )
                )
                await session.commit()

                user_id = await consume_action_token(
                    session,
                    token=token,
                    purpose="password_reset",
                )
                result = await session.execute(
                    select(UserActionToken.__table__.c.used_at).where(
                        UserActionToken.__table__.c.token_hash == token_hash
                    )
                )
                used_at = result.scalar_one()

            await engine.dispose()
            return user_id, used_at

        consumed_user_id, used_at = asyncio.run(verify_expired_token())
        self.assertIsNone(consumed_user_id)
        self.assertIsNone(used_at)


if __name__ == "__main__":
    unittest.main()
