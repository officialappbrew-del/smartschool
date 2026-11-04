import csv
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import smtplib
import threading
import time
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from email.message import EmailMessage
from typing import Annotated, Any, Literal, TypedDict
from urllib.parse import quote, urlsplit, urlunsplit
import asyncio
from pathlib import Path

import jwt
import httpx
from cryptography.fernet import Fernet, InvalidToken
from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from alembic.util.exc import CommandError
from fastapi_tenancy.core.exceptions import MigrationError, TenancyError, TenantNotFoundError
from fastapi_tenancy.core.types import Tenant, TenantStatus
from fastapi_tenancy.migrations.manager import TenantMigrationManager
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pwdlib import PasswordHash
from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, MetaData, Numeric, String, Table, Text, UniqueConstraint, and_, case, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from fastapi_tenancy import TenancyConfig, TenancyManager, TenancyMiddleware
from fastapi_tenancy.resolution.subdomain import SubdomainTenantResolver
from fastapi_tenancy.storage.database import SQLAlchemyTenantStore
from dotenv import load_dotenv


RESERVED_TENANT_IDENTIFIERS = {"admin", "api", "app", "www"}
JWT_ISSUER = "northstar-school-api"
PLATFORM_ADMIN_JWT_ISSUER = "northstar-platform-admin"
PLATFORM_ADMIN_JWT_AUDIENCE = "northstar-platform-dashboard"
JWT_ALGORITHM = "HS256"
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
logger = logging.getLogger(__name__)
_alembic_command_lock = threading.Lock()
password_hasher = PasswordHash.recommended()
DUMMY_PASSWORD_HASH = password_hasher.hash("not-a-real-password")


class TenantBase(DeclarativeBase):
    pass


class PlatformAdminBase(DeclarativeBase):
    pass


class PlatformAdmin(PlatformAdminBase):
    __tablename__ = "platform_admins"
    __table_args__ = (UniqueConstraint("email", name="uq_platform_admins_email"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlatformAuditEvent(PlatformAdminBase):
    __tablename__ = "platform_audit_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    admin_id: Mapped[str] = mapped_column(String(32), nullable=False)
    admin_email: Mapped[str] = mapped_column(String(254), nullable=False)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    tenant_identifier: Mapped[str | None] = mapped_column(String(63))
    summary: Mapped[str] = mapped_column(String(300), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class SchoolProfile(TenantBase):
    __tablename__ = "school_profile"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TenantUser(TenantBase):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="school_admin")
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserActionToken(TenantBase):
    __tablename__ = "user_action_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Student(TenantBase):
    __tablename__ = "students"
    __table_args__ = (UniqueConstraint("admission_number", name="uq_students_admission_number"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    admission_number: Mapped[str] = mapped_column(String(48), nullable=False)
    first_name: Mapped[str] = mapped_column(String(80), nullable=False)
    last_name: Mapped[str] = mapped_column(String(80), nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    gender: Mapped[str] = mapped_column(String(24), nullable=False)
    class_name: Mapped[str] = mapped_column(String(80), nullable=False)
    academic_year: Mapped[str] = mapped_column(String(32), nullable=False)
    guardian_name: Mapped[str] = mapped_column(String(160), nullable=False)
    guardian_email: Mapped[str | None] = mapped_column(String(254))
    guardian_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    medical_notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AcademicYear(TenantBase):
    __tablename__ = "academic_years"
    __table_args__ = (
        UniqueConstraint("name", name="uq_academic_years_name"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AcademicTerm(TenantBase):
    __tablename__ = "academic_terms"
    __table_args__ = (
        UniqueConstraint("academic_year_id", "name", name="uq_academic_terms_year_name"),
        CheckConstraint("end_date >= start_date", name="ck_academic_terms_date_range"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    academic_year_id: Mapped[str] = mapped_column(
        ForeignKey("academic_years.id"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClassSection(TenantBase):
    __tablename__ = "class_sections"
    __table_args__ = (
        UniqueConstraint("name", "academic_year_id", name="uq_class_sections_name_year"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    academic_year_id: Mapped[str] = mapped_column(
        ForeignKey("academic_years.id"),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StudentEnrollment(TenantBase):
    __tablename__ = "student_enrollments"
    __table_args__ = (
        UniqueConstraint("student_id", "class_section_id", name="uq_enrollments_student_class"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"), nullable=False)
    class_section_id: Mapped[str] = mapped_column(ForeignKey("class_sections.id"), nullable=False)
    enrolled_at: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AttendanceRecord(TenantBase):
    __tablename__ = "attendance_records"
    __table_args__ = (
        UniqueConstraint(
            "student_id",
            "class_section_id",
            "attendance_date",
            name="uq_attendance_student_class_date",
        ),
        UniqueConstraint("local_id", name="uq_attendance_local_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"), nullable=False)
    class_section_id: Mapped[str] = mapped_column(
        ForeignKey("class_sections.id"),
        nullable=False,
    )
    attendance_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    marked_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    marked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    local_id: Mapped[str] = mapped_column(String(36), nullable=False)
    client_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Assessment(TenantBase):
    __tablename__ = "assessments"
    __table_args__ = (
        UniqueConstraint(
            "class_section_id",
            "subject",
            "title",
            "assessment_date",
            name="uq_assessments_class_subject_title_date",
        ),
        CheckConstraint("max_score > 0", name="ck_assessments_max_score_positive"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    academic_term_id: Mapped[str | None] = mapped_column(ForeignKey("academic_terms.id"))
    class_section_id: Mapped[str] = mapped_column(
        ForeignKey("class_sections.id"),
        nullable=False,
    )
    subject: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    assessment_date: Mapped[date] = mapped_column(Date, nullable=False)
    max_score: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GradeEntry(TenantBase):
    __tablename__ = "grade_entries"
    __table_args__ = (
        UniqueConstraint("assessment_id", "student_id", name="uq_grades_assessment_student"),
        CheckConstraint("score >= 0", name="ck_grades_score_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id"), nullable=False)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"), nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TeacherClassAssignment(TenantBase):
    __tablename__ = "teacher_class_assignments"
    __table_args__ = (
        UniqueConstraint(
            "teacher_user_id",
            "class_section_id",
            name="uq_teacher_class_assignment",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    teacher_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    class_section_id: Mapped[str] = mapped_column(ForeignKey("class_sections.id"), nullable=False)
    assigned_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BillingSettings(TenantBase):
    __tablename__ = "billing_settings"
    __table_args__ = (
        CheckConstraint("provider IN ('manual', 'paystack', 'stripe')", name="ck_billing_settings_provider"),
        CheckConstraint("currency = upper(currency)", name="ck_billing_settings_currency_upper"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    provider: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="NGN")
    public_key: Mapped[str | None] = mapped_column(String(255))
    credentials_encrypted: Mapped[str | None] = mapped_column(Text)
    updated_by_user_id: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FeeStructure(TenantBase):
    __tablename__ = "fee_structures"
    __table_args__ = (
        UniqueConstraint("class_section_id", "term_id", "description", name="uq_fee_structures_class_term_description"),
        CheckConstraint("amount > 0", name="ck_fee_structures_amount_positive"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    class_section_id: Mapped[str] = mapped_column(ForeignKey("class_sections.id"), nullable=False)
    term_id: Mapped[str] = mapped_column(ForeignKey("academic_terms.id"), nullable=False)
    description: Mapped[str] = mapped_column(String(120), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Invoice(TenantBase):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("invoice_number", name="uq_invoices_invoice_number"),
        UniqueConstraint("enrollment_id", "term_id", name="uq_invoices_enrollment_term"),
        CheckConstraint("amount > 0", name="ck_invoices_amount_positive"),
        CheckConstraint("status IN ('issued', 'paid', 'overdue')", name="ck_invoices_status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    invoice_number: Mapped[str] = mapped_column(String(48), nullable=False)
    enrollment_id: Mapped[str] = mapped_column(ForeignKey("student_enrollments.id"), nullable=False)
    term_id: Mapped[str] = mapped_column(ForeignKey("academic_terms.id"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    line_items: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="issued")
    issued_at: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Payment(TenantBase):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("reference", name="uq_payments_reference"),
        CheckConstraint("amount > 0", name="ck_payments_amount_positive"),
        CheckConstraint("method IN ('cash', 'bank_transfer', 'paystack', 'stripe')", name="ck_payments_method"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    method: Mapped[str] = mapped_column(String(24), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(128))
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Announcement(TenantBase):
    __tablename__ = "announcements"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'published')", name="ck_announcements_status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnnouncementDelivery(TenantBase):
    __tablename__ = "announcement_deliveries"
    __table_args__ = (
        UniqueConstraint("announcement_id", "recipient_email", name="uq_announcement_delivery_recipient"),
        CheckConstraint(
            "status IN ('pending', 'sent', 'failed')",
            name="ck_announcement_delivery_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    announcement_id: Mapped[str] = mapped_column(ForeignKey("announcements.id"), nullable=False)
    recipient_email: Mapped[str] = mapped_column(String(254), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MessageThread(TenantBase):
    __tablename__ = "message_threads"
    __table_args__ = (
        UniqueConstraint(
            "student_id",
            "teacher_user_id",
            "guardian_user_id",
            name="uq_message_threads_participants",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"), nullable=False)
    teacher_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    guardian_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ThreadMessage(TenantBase):
    __tablename__ = "thread_messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("message_threads.id"), nullable=False)
    sender_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MessageFlag(TenantBase):
    __tablename__ = "message_flags"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_message_flags_message"),
        CheckConstraint(
            "status IN ('open', 'reviewed', 'dismissed')",
            name="ck_message_flags_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("thread_messages.id"), nullable=False)
    reported_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TimetableEntry(TenantBase):
    __tablename__ = "timetable_entries"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_timetable_entries_weekday"),
        CheckConstraint("end_time > start_time", name="ck_timetable_entries_time_range"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    class_section_id: Mapped[str] = mapped_column(ForeignKey("class_sections.id"), nullable=False)
    teacher_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    subject: Mapped[str] = mapped_column(String(100), nullable=False)
    room: Mapped[str | None] = mapped_column(String(80))
    weekday: Mapped[int] = mapped_column(nullable=False)
    start_time: Mapped[str] = mapped_column(String(5), nullable=False)
    end_time: Mapped[str] = mapped_column(String(5), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StaffProfile(TenantBase):
    __tablename__ = "staff_profiles"
    __table_args__ = (
        UniqueConstraint("staff_number", name="uq_staff_profiles_staff_number"),
        UniqueConstraint("linked_user_id", name="uq_staff_profiles_linked_user"),
        CheckConstraint("status IN ('active', 'inactive')", name="ck_staff_profiles_status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    linked_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    staff_number: Mapped[str] = mapped_column(String(48), nullable=False)
    first_name: Mapped[str] = mapped_column(String(80), nullable=False)
    last_name: Mapped[str] = mapped_column(String(80), nullable=False)
    email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(32))
    job_title: Mapped[str] = mapped_column(String(100), nullable=False)
    department: Mapped[str | None] = mapped_column(String(100))
    hire_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StaffQualification(TenantBase):
    __tablename__ = "staff_qualifications"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    staff_profile_id: Mapped[str] = mapped_column(ForeignKey("staff_profiles.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    institution: Mapped[str] = mapped_column(String(160), nullable=False)
    field_of_study: Mapped[str | None] = mapped_column(String(120))
    awarded_on: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StaffContract(TenantBase):
    __tablename__ = "staff_contracts"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'active', 'ended')", name="ck_staff_contracts_status"),
        CheckConstraint(
            "end_date IS NULL OR end_date >= start_date",
            name="ck_staff_contracts_date_range",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    staff_profile_id: Mapped[str] = mapped_column(ForeignKey("staff_profiles.id"), nullable=False)
    contract_type: Mapped[str] = mapped_column(String(80), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    reference: Mapped[str | None] = mapped_column(String(80))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def tenant_tables(session: AsyncSession) -> tuple[Table, Table]:
    prefix = session.info.get("table_prefix")
    if prefix is None:
        return TenantUser.__table__, UserActionToken.__table__

    metadata = MetaData()
    user_table = TenantUser.__table__.to_metadata(
        metadata,
        name=f"{prefix}{TenantUser.__tablename__}",
    )
    token_table = UserActionToken.__table__.to_metadata(
        metadata,
        name=f"{prefix}{UserActionToken.__tablename__}",
    )
    return user_table, token_table


def tenant_users_table(session: AsyncSession) -> Table:
    return tenant_tables(session)[0]


def tenant_action_tokens_table(session: AsyncSession) -> Table:
    return tenant_tables(session)[1]


def tenant_students_table(session: AsyncSession) -> Table:
    prefix = session.info.get("table_prefix")
    if prefix is None:
        return Student.__table__

    return Student.__table__.to_metadata(
        MetaData(),
        name=f"{prefix}{Student.__tablename__}",
    )


def tenant_academic_tables(session: AsyncSession) -> tuple[Table, Table, Table, Table]:
    prefix = session.info.get("table_prefix")
    if prefix is None:
        return (
            Student.__table__,
            AcademicYear.__table__,
            ClassSection.__table__,
            StudentEnrollment.__table__,
        )

    metadata = MetaData()
    student_table = Student.__table__.to_metadata(
        metadata,
        name=f"{prefix}{Student.__tablename__}",
    )
    year_table = AcademicYear.__table__.to_metadata(
        metadata,
        name=f"{prefix}{AcademicYear.__tablename__}",
    )
    class_table = ClassSection.__table__.to_metadata(
        metadata,
        name=f"{prefix}{ClassSection.__tablename__}",
    )
    enrollment_table = StudentEnrollment.__table__.to_metadata(
        metadata,
        name=f"{prefix}{StudentEnrollment.__tablename__}",
    )
    return student_table, year_table, class_table, enrollment_table


def tenant_attendance_table(session: AsyncSession) -> Table:
    prefix = session.info.get("table_prefix")
    if prefix is None:
        return AttendanceRecord.__table__

    return AttendanceRecord.__table__.to_metadata(
        MetaData(),
        name=f"{prefix}{AttendanceRecord.__tablename__}",
    )


def tenant_assessment_tables(session: AsyncSession) -> tuple[Table, Table]:
    prefix = session.info.get("table_prefix")
    if prefix is None:
        return Assessment.__table__, GradeEntry.__table__

    metadata = MetaData()
    assessment_table = Assessment.__table__.to_metadata(
        metadata,
        name=f"{prefix}{Assessment.__tablename__}",
    )
    grade_table = GradeEntry.__table__.to_metadata(
        metadata,
        name=f"{prefix}{GradeEntry.__tablename__}",
    )
    return assessment_table, grade_table


def tenant_teacher_assignment_table(session: AsyncSession) -> Table:
    prefix = session.info.get("table_prefix")
    if prefix is None:
        return TeacherClassAssignment.__table__

    return TeacherClassAssignment.__table__.to_metadata(
        MetaData(),
        name=f"{prefix}{TeacherClassAssignment.__tablename__}",
    )


def tenant_academic_term_table(session: AsyncSession) -> Table:
    prefix = session.info.get("table_prefix")
    if prefix is None:
        return AcademicTerm.__table__

    return AcademicTerm.__table__.to_metadata(
        MetaData(),
        name=f"{prefix}{AcademicTerm.__tablename__}",
    )


def tenant_billing_tables(session: AsyncSession) -> tuple[Table, Table, Table, Table]:
    prefix = session.info.get("table_prefix")
    if prefix is None:
        return (
            BillingSettings.__table__,
            FeeStructure.__table__,
            Invoice.__table__,
            Payment.__table__,
        )

    metadata = MetaData()
    settings_table = BillingSettings.__table__.to_metadata(
        metadata,
        name=f"{prefix}{BillingSettings.__tablename__}",
    )
    fee_table = FeeStructure.__table__.to_metadata(
        metadata,
        name=f"{prefix}{FeeStructure.__tablename__}",
    )
    invoice_table = Invoice.__table__.to_metadata(
        metadata,
        name=f"{prefix}{Invoice.__tablename__}",
    )
    payment_table = Payment.__table__.to_metadata(
        metadata,
        name=f"{prefix}{Payment.__tablename__}",
    )
    return settings_table, fee_table, invoice_table, payment_table


def tenant_announcement_tables(session: AsyncSession) -> tuple[Table, Table]:
    prefix = session.info.get("table_prefix")
    if prefix is None:
        return Announcement.__table__, AnnouncementDelivery.__table__

    metadata = MetaData()
    announcement_table = Announcement.__table__.to_metadata(
        metadata,
        name=f"{prefix}{Announcement.__tablename__}",
    )
    delivery_table = AnnouncementDelivery.__table__.to_metadata(
        metadata,
        name=f"{prefix}{AnnouncementDelivery.__tablename__}",
    )
    return announcement_table, delivery_table


def tenant_messaging_tables(session: AsyncSession) -> tuple[Table, Table, Table]:
    prefix = session.info.get("table_prefix")
    if prefix is None:
        return MessageThread.__table__, ThreadMessage.__table__, MessageFlag.__table__

    metadata = MetaData()
    thread_table = MessageThread.__table__.to_metadata(
        metadata,
        name=f"{prefix}{MessageThread.__tablename__}",
    )
    message_table = ThreadMessage.__table__.to_metadata(
        metadata,
        name=f"{prefix}{ThreadMessage.__tablename__}",
    )
    flag_table = MessageFlag.__table__.to_metadata(
        metadata,
        name=f"{prefix}{MessageFlag.__tablename__}",
    )
    return thread_table, message_table, flag_table


def tenant_timetable_table(session: AsyncSession) -> Table:
    prefix = session.info.get("table_prefix")
    if prefix is None:
        return TimetableEntry.__table__

    return TimetableEntry.__table__.to_metadata(
        MetaData(),
        name=f"{prefix}{TimetableEntry.__tablename__}",
    )


def tenant_staff_tables(session: AsyncSession) -> tuple[Table, Table, Table]:
    prefix = session.info.get("table_prefix")
    if prefix is None:
        return StaffProfile.__table__, StaffQualification.__table__, StaffContract.__table__

    metadata = MetaData()
    profile_table = StaffProfile.__table__.to_metadata(
        metadata,
        name=f"{prefix}{StaffProfile.__tablename__}",
    )
    qualification_table = StaffQualification.__table__.to_metadata(
        metadata,
        name=f"{prefix}{StaffQualification.__tablename__}",
    )
    contract_table = StaffContract.__table__.to_metadata(
        metadata,
        name=f"{prefix}{StaffContract.__tablename__}",
    )
    return profile_table, qualification_table, contract_table


def billing_fernet() -> Fernet:
    key = os.getenv("BILLING_ENCRYPTION_KEY")
    if not key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tenant payment credentials cannot be saved until BILLING_ENCRYPTION_KEY is configured",
        )
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="BILLING_ENCRYPTION_KEY is invalid",
        ) from error


def decrypt_billing_credentials(encrypted: str | None) -> dict[str, dict[str, str]]:
    if not encrypted:
        return {}
    try:
        decoded = billing_fernet().decrypt(encrypted.encode("ascii"))
        value = json.loads(decoded)
    except (
        InvalidToken,
        UnicodeEncodeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as error:
        logger.exception("Could not decrypt tenant billing credentials")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tenant payment credentials could not be decrypted; check BILLING_ENCRYPTION_KEY",
        ) from error
    if not isinstance(value, dict):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tenant payment credentials are invalid",
        )
    return {
        provider: {key: item for key, item in values.items() if isinstance(item, str)}
        for provider, values in value.items()
        if provider in {"paystack", "stripe"} and isinstance(values, dict)
    }


def encrypt_billing_credentials(credentials: dict[str, dict[str, str]]) -> str:
    payload = json.dumps(credentials, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return billing_fernet().encrypt(payload).decode("ascii")


def currency_minor_unit_exponent(currency: str) -> int:
    if currency in {
        "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "MGA",
        "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF",
    }:
        return 0
    if currency in {"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"}:
        return 3
    return 2


def amount_to_minor_units(amount: Decimal, currency: str) -> int:
    exponent = currency_minor_unit_exponent(currency)
    scaled_amount = amount * (10**exponent)
    if scaled_amount != scaled_amount.to_integral_value():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invoice amount cannot be charged in the smallest unit of {currency}",
        )
    return int(scaled_amount)


def amount_from_minor_units(amount: int, currency: str) -> Decimal:
    return Decimal(amount) / (10**currency_minor_unit_exponent(currency))


def tenant_app_url(tenant: Tenant, path: str) -> str:
    base_url = os.getenv("PUBLIC_APP_BASE_URL", "http://localhost:5173")
    base = urlsplit(base_url)
    domain_suffix = os.getenv("TENANCY_DOMAIN_SUFFIX", ".yoursms.com").lstrip(".")
    host = f"{tenant.identifier}.{domain_suffix}"
    if base.port is not None:
        host = f"{host}:{base.port}"
    return urlunsplit((base.scheme, host, path, "", ""))


async def ensure_academic_year(session: AsyncSession, *, name: str) -> str:
    _, year_table, _, _ = tenant_academic_tables(session)
    result = await session.execute(
        select(year_table.c.id).where(year_table.c.name == name)
    )
    year_id = result.scalar_one_or_none()
    if year_id is not None:
        return year_id

    year_id = uuid.uuid4().hex
    await session.execute(
        insert(year_table).values(
            id=year_id,
            name=name,
            is_current=False,
        )
    )
    return year_id


async def ensure_class_section(
    session: AsyncSession,
    *,
    name: str,
    academic_year: str,
) -> str:
    _, _, class_table, _ = tenant_academic_tables(session)
    academic_year_id = await ensure_academic_year(session, name=academic_year)
    result = await session.execute(
        select(class_table.c.id).where(
            class_table.c.name == name,
            class_table.c.academic_year_id == academic_year_id,
        )
    )
    class_id = result.scalar_one_or_none()
    if class_id is not None:
        return class_id

    class_id = uuid.uuid4().hex
    await session.execute(
        insert(class_table).values(
            id=class_id,
            name=name,
            academic_year_id=academic_year_id,
            is_active=True,
        )
    )
    return class_id


async def ensure_student_enrollment(
    session: AsyncSession,
    *,
    student_id: str,
    class_section_id: str,
    enrolled_at: date,
) -> None:
    _, year_table, class_table, enrollment_table = tenant_academic_tables(session)
    new_class_result = await session.execute(
        select(year_table.c.name)
        .select_from(
            class_table.join(
                year_table,
                class_table.c.academic_year_id == year_table.c.id,
            )
        )
        .where(class_table.c.id == class_section_id)
    )
    academic_year = new_class_result.scalar_one()

    current_result = await session.execute(
        select(enrollment_table.c.id)
        .select_from(
            enrollment_table.join(
                class_table,
                enrollment_table.c.class_section_id == class_table.c.id,
            ).join(
                year_table,
                class_table.c.academic_year_id == year_table.c.id,
            )
        )
        .where(
            enrollment_table.c.student_id == student_id,
            enrollment_table.c.status == "active",
            year_table.c.name == academic_year,
        )
    )
    current_enrollment_id = current_result.scalar_one_or_none()
    if current_enrollment_id is not None:
        current_class_result = await session.execute(
            select(enrollment_table.c.class_section_id).where(
                enrollment_table.c.id == current_enrollment_id
            )
        )
        current_class_id = current_class_result.scalar_one()
        if current_class_id == class_section_id:
            return
        await session.execute(
            update(enrollment_table)
            .where(enrollment_table.c.id == current_enrollment_id)
            .values(status="transferred")
        )

    same_class_result = await session.execute(
        select(enrollment_table.c.id).where(
            enrollment_table.c.student_id == student_id,
            enrollment_table.c.class_section_id == class_section_id,
        )
    )
    existing_enrollment_id = same_class_result.scalar_one_or_none()
    if existing_enrollment_id is not None:
        await session.execute(
            update(enrollment_table)
            .where(enrollment_table.c.id == existing_enrollment_id)
            .values(status="active", enrolled_at=enrolled_at)
        )
    else:
        await session.execute(
            insert(enrollment_table).values(
                id=uuid.uuid4().hex,
                student_id=student_id,
                class_section_id=class_section_id,
                enrolled_at=enrolled_at,
                status="active",
            )
        )


async def get_request_tenant(request: Request) -> Tenant:
    tenant = getattr(request.state, "tenant", None)
    if not isinstance(tenant, Tenant):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant context is unavailable",
        )
    if tenant.status != TenantStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This school tenant is not active",
        )
    return tenant


async def platform_db(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory: async_sessionmaker[AsyncSession] | None = getattr(
        request.app.state,
        "platform_session_factory",
        None,
    )
    if session_factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Platform database is not configured",
        )
    async with session_factory() as session:
        yield session


async def require_platform_admin(
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(platform_db),
) -> PlatformAdmin:
    secret = os.getenv("TENANCY_JWT_SECRET")
    if secret is None or len(secret) < 32:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Platform authentication is not configured",
        )
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Platform administrator sign-in required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = jwt.decode(
            authorization[7:],
            secret,
            algorithms=[JWT_ALGORITHM],
            issuer=PLATFORM_ADMIN_JWT_ISSUER,
            audience=PLATFORM_ADMIN_JWT_AUDIENCE,
            options={"require": ["sub", "email", "exp", "iss", "aud"]},
        )
    except jwt.InvalidTokenError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired platform session",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error

    result = await session.execute(
        select(PlatformAdmin).where(
            PlatformAdmin.id == claims["sub"],
            PlatformAdmin.email == claims["email"],
            PlatformAdmin.is_active.is_(True),
        )
    )
    admin = result.scalar_one_or_none()
    if admin is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or inactive platform account",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return admin


async def find_tenant_user(
    session: AsyncSession,
    *,
    user_id: str | None = None,
    email: str | None = None,
) -> TenantUser | None:
    table = tenant_users_table(session)
    if user_id is not None:
        query = select(table).where(table.c.id == user_id)
    elif email is not None:
        query = select(table).where(func.lower(table.c.email) == email.strip().lower())
    else:
        raise ValueError("A user ID or email is required")

    result = await session.execute(query)
    row = result.mappings().one_or_none()
    return TenantUser(**dict(row)) if row is not None else None


def make_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def make_action_token() -> str:
    return secrets.token_urlsafe(32)


async def store_action_token(
    session: AsyncSession,
    *,
    user_id: str,
    purpose: str,
    token: str,
    expires_at: datetime,
) -> None:
    token_table = tenant_action_tokens_table(session)
    await session.execute(
        update(token_table)
        .where(
            token_table.c.user_id == user_id,
            token_table.c.purpose == purpose,
            token_table.c.used_at.is_(None),
        )
        .values(used_at=datetime.now(UTC))
    )
    await session.execute(
        insert(token_table).values(
            token_hash=make_token_hash(token),
            user_id=user_id,
            purpose=purpose,
            expires_at=expires_at,
            used_at=None,
        )
    )


async def consume_action_token(
    session: AsyncSession,
    *,
    token: str,
    purpose: str,
) -> str | None:
    token_table = tenant_action_tokens_table(session)
    result = await session.execute(
        update(token_table)
        .where(
            token_table.c.token_hash == make_token_hash(token),
            token_table.c.purpose == purpose,
            token_table.c.used_at.is_(None),
            token_table.c.expires_at > datetime.now(UTC),
        )
        .values(used_at=datetime.now(UTC))
        .returning(token_table.c.user_id)
    )
    return result.scalar_one_or_none()


async def revoke_action_token(
    session: AsyncSession,
    *,
    token: str,
    purpose: str,
) -> None:
    token_table = tenant_action_tokens_table(session)
    await session.execute(
        update(token_table)
        .where(
            token_table.c.token_hash == make_token_hash(token),
            token_table.c.purpose == purpose,
            token_table.c.used_at.is_(None),
        )
        .values(used_at=datetime.now(UTC))
    )
    await session.commit()


def email_configuration() -> tuple[str, str, int, str, str | None, bool, bool] | None:
    delivery_mode = os.getenv("EMAIL_DELIVERY_MODE", "sync").strip().lower()
    if delivery_mode != "sync":
        raise ValueError(
            "EMAIL_DELIVERY_MODE must be 'sync'; Celery delivery is not configured"
        )

    host = os.getenv("EMAIL_HOST") or os.getenv("SMTP_HOST")
    sender = os.getenv("DEFAULT_FROM_EMAIL") or os.getenv("SMTP_FROM")
    if not host or not sender:
        return None

    port_value = os.getenv("EMAIL_PORT") or os.getenv("SMTP_PORT", "587")
    try:
        port = int(port_value)
    except ValueError as error:
        raise ValueError("SMTP_PORT must be a valid port number") from error
    if not 1 <= port <= 65535:
        raise ValueError("SMTP_PORT must be between 1 and 65535")

    def enabled_setting(primary: str, fallback: str, default: str) -> bool:
        value = os.getenv(primary)
        if value is None:
            value = os.getenv(fallback, default)
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
        raise ValueError(f"{primary} must be a boolean value")

    use_ssl = enabled_setting("EMAIL_USE_SSL", "SMTP_USE_SSL", "false")
    use_starttls = enabled_setting("EMAIL_USE_TLS", "SMTP_STARTTLS", "true")
    if use_ssl and use_starttls:
        raise ValueError("EMAIL_USE_TLS and EMAIL_USE_SSL cannot both be enabled")

    return (
        host,
        sender,
        port,
        os.getenv("EMAIL_HOST_USER") or os.getenv("SMTP_USERNAME", ""),
        os.getenv("EMAIL_HOST_PASSWORD") or os.getenv("SMTP_PASSWORD"),
        use_ssl,
        use_starttls,
    )


def send_email(recipient: str, subject: str, body: str) -> None:
    settings = email_configuration()
    if settings is None:
        raise RuntimeError("Email delivery is not configured")
    host, sender, port, username, password, use_ssl, use_starttls = settings

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with smtp_class(host, port, timeout=15) as smtp:
        if not use_ssl and use_starttls:
            smtp.starttls()
        if username:
            smtp.login(username, password or "")
        smtp.send_message(message)


def action_url(tenant_identifier: str, path: str, token: str) -> str:
    base_url = os.getenv("PUBLIC_APP_BASE_URL", "http://localhost:3000")
    try:
        parsed = urlsplit(base_url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ValueError("PUBLIC_APP_BASE_URL must be a valid URL") from error
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise ValueError("PUBLIC_APP_BASE_URL must use HTTP or HTTPS and include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("PUBLIC_APP_BASE_URL must not include credentials")

    domain_suffix = os.getenv("TENANCY_DOMAIN_SUFFIX", ".yoursms.com").lstrip(".")
    if hostname == "localhost" or hostname.endswith(f".{domain_suffix}"):
        school_hostname = f"{tenant_identifier}.{hostname}"
    else:
        school_hostname = f"{tenant_identifier}.{domain_suffix}"
    netloc = school_hostname
    if port is not None:
        netloc = f"{netloc}:{port}"
    query = f"token={quote(token, safe='')}"
    return urlunsplit((parsed.scheme, netloc, path, query, ""))


async def deliver_action_email(recipient: str, subject: str, body: str) -> None:
    await asyncio.to_thread(send_email, recipient, subject, body)


class TenantRegistration(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    identifier: str = Field(
        min_length=3,
        max_length=48,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    name: str = Field(min_length=1, max_length=160)

    @field_validator("identifier")
    @classmethod
    def reject_reserved_identifiers(cls, value: str) -> str:
        if value in RESERVED_TENANT_IDENTIFIERS:
            raise ValueError("This subdomain is reserved")
        return value


class TenantRegistrationResponse(BaseModel):
    identifier: str
    name: str


class TenantSummary(BaseModel):
    identifier: str
    name: str
    status: str
    migration_status: Literal["current", "behind", "not_migrated", "unknown"]
    current_revision: str | None
    head_revision: str


class TenantStatusResponse(BaseModel):
    identifier: str
    name: str
    status: str


class TenantListResponse(BaseModel):
    items: list[TenantSummary]
    total: int


class PlatformAuditEventResponse(BaseModel):
    id: str
    admin_email: str
    action: str
    tenant_identifier: str | None
    summary: str
    occurred_at: datetime


class PlatformAuditEventListResponse(BaseModel):
    items: list[PlatformAuditEventResponse]


class TenantStatusUpdate(BaseModel):
    status: Literal["active", "suspended"]


class PlatformAdminLogin(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value


class PlatformAdminTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    email: str


class TenantAdminProvision(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=EMAIL_PATTERN)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value


class TeacherInvitationRequest(TenantAdminProvision):
    class_section_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("class_section_ids")
    @classmethod
    def reject_duplicate_classes(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("Class section IDs must be unique")
        if any(len(class_id) != 32 for class_id in value):
            raise ValueError("Class section IDs must be 32 characters")
        return value


class TeacherAssignmentUpdate(BaseModel):
    class_section_ids: list[str] = Field(max_length=100)

    @field_validator("class_section_ids")
    @classmethod
    def reject_duplicate_classes(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("Class section IDs must be unique")
        if any(len(class_id) != 32 for class_id in value):
            raise ValueError("Class section IDs must be 32 characters")
        return value


class SchoolLogin(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value


class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=EMAIL_PATTERN)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value


class TokenPasswordSet(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    password: str = Field(min_length=12, max_length=128)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class StudentAdmission(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    admission_number: str = Field(min_length=1, max_length=48)
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    date_of_birth: date
    gender: str = Field(pattern=r"^(female|male|other|prefer_not_to_say)$")
    class_name: str = Field(min_length=1, max_length=80)
    academic_year: str = Field(min_length=4, max_length=32)
    guardian_name: str = Field(min_length=1, max_length=160)
    guardian_email: str | None = Field(default=None, max_length=254, pattern=EMAIL_PATTERN)
    guardian_phone: str = Field(min_length=1, max_length=32)
    medical_notes: str | None = Field(default=None, max_length=2000)

    @field_validator("admission_number", mode="before")
    @classmethod
    def normalize_admission_number(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("gender", mode="before")
    @classmethod
    def normalize_gender(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("guardian_email", mode="before")
    @classmethod
    def normalize_guardian_email(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower() or None
        return value

    @field_validator("date_of_birth")
    @classmethod
    def reject_future_birth_date(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("Date of birth cannot be in the future")
        return value


class StudentUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    admission_number: str | None = Field(default=None, min_length=1, max_length=48)
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)
    date_of_birth: date | None = None
    gender: str | None = Field(
        default=None,
        pattern=r"^(female|male|other|prefer_not_to_say)$",
    )
    class_name: str | None = Field(default=None, min_length=1, max_length=80)
    academic_year: str | None = Field(default=None, min_length=4, max_length=32)
    guardian_name: str | None = Field(default=None, min_length=1, max_length=160)
    guardian_email: str | None = Field(default=None, max_length=254, pattern=EMAIL_PATTERN)
    guardian_phone: str | None = Field(default=None, min_length=1, max_length=32)
    medical_notes: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None

    @field_validator("guardian_email", mode="before")
    @classmethod
    def normalize_guardian_email(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower() or None
        return value

    @field_validator("admission_number", mode="before")
    @classmethod
    def normalize_admission_number(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("gender", mode="before")
    @classmethod
    def normalize_gender(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("date_of_birth")
    @classmethod
    def reject_future_birth_date(cls, value: date | None) -> date | None:
        if value is not None and value > date.today():
            raise ValueError("Date of birth cannot be in the future")
        return value


class StudentResponse(BaseModel):
    id: str
    admission_number: str
    first_name: str
    last_name: str
    date_of_birth: date
    gender: str
    class_name: str
    academic_year: str
    guardian_name: str
    guardian_email: str | None
    guardian_phone: str
    medical_notes: str | None
    is_active: bool


class StudentListResponse(BaseModel):
    items: list[StudentResponse]
    total: int


class StudentImportResponse(BaseModel):
    imported: int
    students: list[StudentResponse]


class ClassSectionCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    academic_year: str = Field(min_length=4, max_length=32)


class AcademicYearCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=4, max_length=32)
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = False


class AcademicYearResponse(BaseModel):
    id: str
    name: str
    start_date: date | None
    end_date: date | None
    is_current: bool
    class_count: int


class AcademicTermCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    start_date: date
    end_date: date


class AcademicTermResponse(BaseModel):
    id: str
    academic_year_id: str
    academic_year: str
    name: str
    start_date: date
    end_date: date


class BillingSettingsUpdate(BaseModel):
    provider: Literal["manual", "paystack", "stripe"]
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    public_key: str | None = Field(default=None, max_length=255)
    secret_key: str | None = Field(default=None, min_length=1, max_length=500)
    webhook_secret: str | None = Field(default=None, min_length=1, max_length=500)
    clear_credentials: bool = False


class BillingSettingsResponse(BaseModel):
    provider: Literal["manual", "paystack", "stripe"]
    currency: str
    public_key: str | None
    has_secret_key: bool
    has_webhook_secret: bool
    configured: bool


class ParentBillingSettingsResponse(BaseModel):
    provider: Literal["manual", "paystack", "stripe"]
    currency: str
    configured: bool


class AnnouncementCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=10000)


class AnnouncementResponse(BaseModel):
    id: str
    title: str
    body: str
    status: Literal["draft", "published"]
    created_at: datetime
    published_at: datetime | None
    recipient_count: int
    delivered_count: int
    failed_count: int
    pending_count: int


class AnnouncementListResponse(BaseModel):
    items: list[AnnouncementResponse]
    total: int


class ParentAnnouncementResponse(BaseModel):
    id: str
    title: str
    body: str
    created_at: datetime
    published_at: datetime


class ParentAnnouncementListResponse(BaseModel):
    items: list[ParentAnnouncementResponse]
    total: int


class ParentTeacherContact(BaseModel):
    teacher_id: str
    email: str
    class_name: str


class MessageThreadCreate(BaseModel):
    student_id: str = Field(min_length=32, max_length=32)
    teacher_id: str = Field(min_length=32, max_length=32)


class ThreadMessageCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    body: str = Field(min_length=1, max_length=5000)


class ThreadMessageResponse(BaseModel):
    id: str
    sender_user_id: str
    sender_email: str
    body: str
    created_at: datetime
    flag_status: Literal["open", "reviewed", "dismissed"] | None
    can_flag: bool


class MessageThreadSummary(BaseModel):
    id: str
    student_id: str
    student_name: str
    teacher_id: str
    teacher_email: str
    guardian_id: str
    guardian_email: str
    message_count: int
    latest_message: str | None
    updated_at: datetime


class MessageThreadListResponse(BaseModel):
    items: list[MessageThreadSummary]
    total: int


class MessageThreadDetail(MessageThreadSummary):
    messages: list[ThreadMessageResponse]


class MessageFlagCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    reason: str = Field(min_length=5, max_length=500)


class MessageFlagUpdate(BaseModel):
    status: Literal["reviewed", "dismissed"]


class MessageFlagResponse(BaseModel):
    id: str
    message_id: str
    thread_id: str
    student_name: str
    teacher_email: str
    guardian_email: str
    sender_email: str
    reported_by_email: str
    body: str
    reason: str
    status: Literal["open", "reviewed", "dismissed"]
    created_at: datetime
    reviewed_at: datetime | None


class MessageFlagListResponse(BaseModel):
    items: list[MessageFlagResponse]
    total: int


class BillingCheckoutResponse(BaseModel):
    provider: Literal["paystack", "stripe"]
    checkout_url: str
    reference: str


class FeeStructureCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    class_section_id: str = Field(min_length=32, max_length=32)
    term_id: str = Field(min_length=32, max_length=32)
    description: str = Field(min_length=1, max_length=120)
    amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2)


class FeeStructureResponse(BaseModel):
    id: str
    class_section_id: str
    class_name: str
    academic_year: str
    term_id: str
    term_name: str
    description: str
    amount: Decimal
    currency: str
    is_active: bool


class FeeStructureStatusUpdate(BaseModel):
    is_active: bool


class InvoiceGenerateRequest(BaseModel):
    class_section_id: str = Field(min_length=32, max_length=32)
    term_id: str = Field(min_length=32, max_length=32)
    due_date: date


class InvoiceLineItemResponse(BaseModel):
    description: str
    amount: Decimal


class PaymentResponse(BaseModel):
    id: str
    amount: Decimal
    method: Literal["cash", "bank_transfer", "paystack", "stripe"]
    reference: str | None
    paid_at: datetime
    voided_at: datetime | None


class InvoiceResponse(BaseModel):
    id: str
    invoice_number: str
    student_id: str
    student_name: str
    admission_number: str
    class_section_id: str
    class_name: str
    term_id: str
    term_name: str
    amount: Decimal
    currency: str
    balance: Decimal
    status: Literal["issued", "paid", "overdue"]
    issued_at: date
    due_date: date
    line_items: list[InvoiceLineItemResponse]
    payments: list[PaymentResponse]


class InvoiceListResponse(BaseModel):
    items: list[InvoiceResponse]
    total: int


class BillingCurrencySummary(BaseModel):
    currency: str
    total_invoiced: Decimal
    total_collected: Decimal
    total_outstanding: Decimal
    collection_rate: Decimal
    invoice_count: int
    paid_invoice_count: int
    overdue_invoice_count: int


class BillingDefaulterResponse(BaseModel):
    invoice_id: str
    invoice_number: str
    student_id: str
    student_name: str
    admission_number: str
    class_name: str
    term_name: str
    due_date: date
    days_overdue: int
    balance: Decimal
    currency: str


class BillingDashboardResponse(BaseModel):
    summaries: list[BillingCurrencySummary]
    defaulters: list[BillingDefaulterResponse]
    defaulters_total: int


class AnalyticsEnrollmentPoint(BaseModel):
    month: str
    enrollments: int


class AnalyticsAttendancePoint(BaseModel):
    month: str
    rate: Decimal | None
    marked_count: int


class AnalyticsAttendanceSummary(BaseModel):
    rate: Decimal | None
    marked_count: int
    present_count: int
    late_count: int
    absent_count: int
    excused_count: int
    trend: list[AnalyticsAttendancePoint]


class GradeDistributionBand(BaseModel):
    band: Literal["0-49", "50-59", "60-69", "70-79", "80-100"]
    count: int


class AnalyticsDashboardResponse(BaseModel):
    active_students: int
    active_enrollments: int
    active_classes: int
    enrollment_trend: list[AnalyticsEnrollmentPoint]
    attendance: AnalyticsAttendanceSummary
    fee_summaries: list[BillingCurrencySummary]
    grade_distribution: list[GradeDistributionBand]
    average_grade: Decimal | None
    graded_count: int


class InvoiceGenerateResponse(BaseModel):
    created: int
    skipped: int
    invoices: list[InvoiceResponse]


class PaymentCreate(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    method: Literal["cash", "bank_transfer"]
    reference: str | None = Field(default=None, max_length=128)
    paid_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("paid_at")
    @classmethod
    def require_payment_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Payment timestamp must include a timezone")
        return value


class PaymentVoidRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


class ClassSectionResponse(BaseModel):
    id: str
    name: str
    academic_year_id: str
    academic_year: str
    is_active: bool
    student_count: int


class TimetableEntryCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    class_section_id: str = Field(min_length=32, max_length=32)
    teacher_id: str = Field(min_length=32, max_length=32)
    subject: str = Field(min_length=1, max_length=100)
    room: str | None = Field(default=None, max_length=80)
    weekday: int = Field(ge=0, le=6)
    start_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    end_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")

    @field_validator("room", mode="before")
    @classmethod
    def normalize_room(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("end_time")
    @classmethod
    def require_positive_duration(cls, value: str, info: Any) -> str:
        start_time = info.data.get("start_time")
        if start_time is not None and value <= start_time:
            raise ValueError("End time must be after start time")
        return value


class TimetableEntryResponse(BaseModel):
    id: str
    class_section_id: str
    class_name: str
    teacher_id: str
    teacher_email: str
    subject: str
    room: str | None
    weekday: int
    start_time: str
    end_time: str


class StaffProfileCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    staff_number: str = Field(min_length=1, max_length=48)
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    job_title: str = Field(min_length=1, max_length=100)
    linked_user_id: str | None = Field(default=None, min_length=32, max_length=32)
    email: str | None = Field(default=None, max_length=254, pattern=EMAIL_PATTERN)
    phone: str | None = Field(default=None, max_length=32)
    department: str | None = Field(default=None, max_length=100)
    hire_date: date | None = None
    notes: str | None = Field(default=None, max_length=5000)

    @field_validator("staff_number", mode="before")
    @classmethod
    def normalize_staff_number(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("email", mode="before")
    @classmethod
    def normalize_staff_email(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower() or None
        return value

    @field_validator("phone", "department", "notes", mode="before")
    @classmethod
    def normalize_optional_staff_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value


class StaffProfileUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)
    job_title: str | None = Field(default=None, min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=254, pattern=EMAIL_PATTERN)
    phone: str | None = Field(default=None, max_length=32)
    department: str | None = Field(default=None, max_length=100)
    hire_date: date | None = None
    status: Literal["active", "inactive"] | None = None
    notes: str | None = Field(default=None, max_length=5000)

    @field_validator("first_name", "last_name", "job_title", "status")
    @classmethod
    def reject_null_required_staff_fields(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("This staff field cannot be null")
        return value

    @field_validator("email", mode="before")
    @classmethod
    def normalize_staff_email(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower() or None
        return value

    @field_validator("phone", "department", "notes", mode="before")
    @classmethod
    def normalize_optional_staff_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value


class StaffQualificationCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=160)
    institution: str = Field(min_length=1, max_length=160)
    field_of_study: str | None = Field(default=None, max_length=120)
    awarded_on: date | None = None
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("field_of_study", "notes", mode="before")
    @classmethod
    def normalize_optional_qualification_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value


class StaffQualificationResponse(BaseModel):
    id: str
    title: str
    institution: str
    field_of_study: str | None
    awarded_on: date | None
    notes: str | None


class StaffContractCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    contract_type: str = Field(min_length=1, max_length=80)
    start_date: date
    end_date: date | None = None
    status: Literal["draft", "active"] = "draft"
    reference: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=5000)

    @field_validator("end_date")
    @classmethod
    def validate_contract_dates(cls, value: date | None, info: Any) -> date | None:
        start_date = info.data.get("start_date")
        if value is not None and start_date is not None and value < start_date:
            raise ValueError("Contract end date cannot be before its start date")
        return value

    @field_validator("reference", "notes", mode="before")
    @classmethod
    def normalize_optional_contract_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value


class StaffContractStatusUpdate(BaseModel):
    status: Literal["draft", "active", "ended"]


class StaffContractResponse(BaseModel):
    id: str
    contract_type: str
    start_date: date
    end_date: date | None
    status: Literal["draft", "active", "ended"]
    reference: str | None
    notes: str | None


class StaffProfileResponse(BaseModel):
    id: str
    linked_user_id: str | None
    staff_number: str
    first_name: str
    last_name: str
    email: str | None
    phone: str | None
    job_title: str
    department: str | None
    hire_date: date | None
    status: Literal["active", "inactive"]
    notes: str | None
    qualifications: list[StaffQualificationResponse]
    contracts: list[StaffContractResponse]


class AssessmentCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    term_id: str = Field(min_length=32, max_length=32)
    subject: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=120)
    assessment_date: date
    max_score: Decimal = Field(gt=0, max_digits=8, decimal_places=2)


class AssessmentResponse(BaseModel):
    id: str
    term_id: str | None
    term_name: str | None
    class_section_id: str
    class_name: str
    academic_year: str
    subject: str
    title: str
    assessment_date: date
    max_score: Decimal
    graded_count: int


class GradeEntryInput(BaseModel):
    student_id: str = Field(min_length=32, max_length=32)
    score: Decimal | None = Field(default=None, ge=0, max_digits=8, decimal_places=2)


class GradeSaveRequest(BaseModel):
    entries: list[GradeEntryInput] = Field(max_length=500)

    @field_validator("entries")
    @classmethod
    def reject_duplicate_students(
        cls,
        entries: list[GradeEntryInput],
    ) -> list[GradeEntryInput]:
        student_ids = [entry.student_id for entry in entries]
        if len(set(student_ids)) != len(student_ids):
            raise ValueError("Grade entries must have unique student IDs")
        return entries


class GradebookStudentResponse(BaseModel):
    student_id: str
    admission_number: str
    first_name: str
    last_name: str
    score: Decimal | None
    is_currently_enrolled: bool


class AssessmentGradebookResponse(BaseModel):
    assessment: AssessmentResponse
    students: list[GradebookStudentResponse]


class GradeSaveResponse(BaseModel):
    assessment_id: str
    updated_count: int
    removed_count: int


class ReportCardAssessment(BaseModel):
    subject: str
    title: str
    assessment_date: date
    score: Decimal
    max_score: Decimal
    percentage: Decimal


class ReportCardSubjectResult(BaseModel):
    subject: str
    score_total: Decimal
    max_score_total: Decimal
    percentage: Decimal
    assessment_count: int


class ReportCardAttendance(BaseModel):
    present: int
    late: int
    absent: int
    excused: int
    marked_days: int
    attendance_rate: Decimal | None


class StudentReportCardResponse(BaseModel):
    student_id: str
    admission_number: str
    first_name: str
    last_name: str
    class_name: str
    academic_year: str
    term_name: str
    term_start_date: date
    term_end_date: date
    assessments: list[ReportCardAssessment]
    subjects: list[ReportCardSubjectResult]
    overall_percentage: Decimal | None
    attendance: ReportCardAttendance


class ParentChildResponse(BaseModel):
    id: str
    admission_number: str
    first_name: str
    last_name: str
    class_name: str
    academic_year: str


class ParentAttendanceRecordResponse(BaseModel):
    attendance_date: date
    class_name: str
    status: Literal["present", "absent", "late", "excused"]


class ReportCardSubjectAccumulator(TypedDict):
    score_total: Decimal
    max_score_total: Decimal
    count: int


class ReportCardAccumulator(TypedDict):
    student_id: str
    admission_number: str
    first_name: str
    last_name: str
    assessments: list[ReportCardAssessment]
    subjects: dict[str, ReportCardSubjectAccumulator]


class EnrollmentCreate(BaseModel):
    class_section_id: str = Field(min_length=32, max_length=32)
    enrolled_at: date = Field(default_factory=date.today)


class EnrollmentResponse(BaseModel):
    id: str
    student_id: str
    class_section_id: str
    class_name: str
    academic_year: str
    enrolled_at: date
    status: str


class AttendanceSyncEntry(BaseModel):
    local_id: uuid.UUID
    student_id: str = Field(min_length=32, max_length=32)
    class_section_id: str = Field(min_length=32, max_length=32)
    attendance_date: date
    status: Literal["present", "absent", "late", "excused"]
    client_timestamp: datetime

    @field_validator("client_timestamp")
    @classmethod
    def require_timezone_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Client timestamp must include a timezone")
        return value


class AttendanceSyncRequest(BaseModel):
    entries: list[AttendanceSyncEntry] = Field(min_length=1, max_length=500)

    @field_validator("entries")
    @classmethod
    def reject_duplicate_local_ids(
        cls,
        entries: list[AttendanceSyncEntry],
    ) -> list[AttendanceSyncEntry]:
        local_ids = [entry.local_id for entry in entries]
        if len(set(local_ids)) != len(local_ids):
            raise ValueError("Attendance entries must have unique local IDs")
        return entries


class AttendanceSyncResult(BaseModel):
    local_id: uuid.UUID
    status: Literal["synced", "duplicate"]


class AttendanceSyncResponse(BaseModel):
    results: list[AttendanceSyncResult]


class AttendanceRosterEntry(BaseModel):
    student_id: str
    admission_number: str
    first_name: str
    last_name: str
    attendance_status: Literal["present", "absent", "late", "excused"] | None
    client_timestamp: datetime | None


class AttendanceRosterResponse(BaseModel):
    class_section_id: str
    class_name: str
    academic_year: str
    attendance_date: date
    students: list[AttendanceRosterEntry]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str


class SchoolUserResponse(BaseModel):
    id: str
    email: str
    role: str


class TeacherResponse(BaseModel):
    id: str
    email: str
    is_active: bool
    class_section_ids: list[str]


class ActionMessage(BaseModel):
    message: str


def require_email_configuration() -> None:
    try:
        settings = email_configuration()
    except ValueError as error:
        logger.exception("Invalid email delivery configuration")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email delivery is not configured correctly",
        ) from error
    if settings is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email delivery is not configured",
        )


def read_tenant_current_revision(
    migrator: TenantMigrationManager,
    tenant: Tenant,
) -> str | None:
    config = AlembicConfig(
        Path(__file__).resolve().parents[1] / "alembic.ini",
    )
    x_args = migrator._build_alembic_args(tenant)
    config.attributes.update(x_args)
    config.attributes["x_args"] = x_args
    output = io.StringIO()
    config.stdout = output
    with _alembic_command_lock:
        command.current(config)
    current_output = output.getvalue().strip()
    if not current_output:
        return None

    revisions = [
        line.split(":", 1)[-1].strip().split()[0]
        for line in current_output.splitlines()
        if line.strip()
    ]
    return revisions[-1] if revisions else None


async def record_platform_audit(
    session: AsyncSession,
    admin: PlatformAdmin,
    action: str,
    summary: str,
    tenant_identifier: str | None = None,
) -> None:
    session.add(
        PlatformAuditEvent(
            admin_id=admin.id,
            admin_email=admin.email,
            action=action,
            tenant_identifier=tenant_identifier,
            summary=summary,
        )
    )
    try:
        await session.commit()
    except SQLAlchemyError as error:
        await session.rollback()
        logger.exception("Could not persist platform audit event: %s", action)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The action completed, but its audit record could not be saved",
        ) from error


def build_tenancy_manager() -> TenancyManager | None:
    database_url = os.getenv("TENANCY_DATABASE_URL")
    if not database_url:
        return None

    parsed_database_url = make_url(database_url)
    if parsed_database_url.drivername == "postgresql":
        database_url = parsed_database_url.set(
            drivername="postgresql+asyncpg",
        ).render_as_string(hide_password=False)

    domain_suffix = os.getenv("TENANCY_DOMAIN_SUFFIX", ".yoursms.com")
    config = TenancyConfig(
        database_url=database_url,
        resolution_strategy="custom",
        domain_suffix=domain_suffix,
        isolation_strategy="schema",
        schema_prefix="tenant_",
        cache_enabled=os.getenv("TENANCY_CACHE_ENABLED", "false").lower() == "true",
    )
    store = SQLAlchemyTenantStore(config.database_url)
    resolver = SubdomainTenantResolver(
        store,
        domain_suffix=domain_suffix,
        trust_x_forwarded=False,
    )
    return TenancyManager(config, store, custom_resolver=resolver)


@asynccontextmanager
async def lifespan(app: FastAPI):
    manager: TenancyManager | None = app.state.tenancy_manager
    platform_engine = None
    try:
        if manager is not None:
            await manager.initialize()
            platform_engine = create_async_engine(str(manager.config.database_url))
            async with platform_engine.begin() as connection:
                await connection.run_sync(PlatformAdminBase.metadata.create_all)
            app.state.platform_session_factory = async_sessionmaker(
                platform_engine,
                expire_on_commit=False,
            )
        yield
    finally:
        if platform_engine is not None:
            await platform_engine.dispose()
        if manager is not None:
            await manager.close()


def create_app() -> FastAPI:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    manager = build_tenancy_manager()
    if manager is not None:
        async def tenant_db(request: Request) -> AsyncIterator[AsyncSession]:
            tenant = await get_request_tenant(request)
            async with manager.isolation_provider.get_session(tenant) as session:
                yield session
    else:
        async def tenant_db() -> None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Tenant database is not configured",
            )

    app = FastAPI(
        title="Northstar School API",
        description="Tenant-aware API foundation for the Northstar School Management System.",
        version="0.2.0",
        lifespan=lifespan,
    )
    app.state.tenancy_manager = manager
    app.state.tenant_db_dependency = tenant_db

    if manager is not None:
        app.add_middleware(
            TenancyMiddleware,
            manager=manager,
            excluded_paths=[
                "/health",
                "/docs",
                "/openapi.json",
                "/redoc",
                "/api/v1/admin",
            ],
        )

    frontend_origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")
    frontend_url = urlsplit(frontend_origin)
    tenant_domain = os.getenv("TENANCY_DOMAIN_SUFFIX", ".yoursms.com").lstrip(".")
    frontend_port = f":{frontend_url.port}" if frontend_url.port is not None else ""
    tenant_origin_regex = (
        rf"^{re.escape(frontend_url.scheme)}://"
        rf"(?:[a-z0-9-]+\.)?{re.escape(tenant_domain)}{frontend_port}$"
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[frontend_origin],
        allow_origin_regex=tenant_origin_regex,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/health", tags=["Operations"])
    async def health_check() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"name": "Northstar School API", "docs": "/docs"}

    @app.post(
        "/api/v1/admin/auth/login",
        response_model=PlatformAdminTokenResponse,
        tags=["Platform administration"],
    )
    async def platform_admin_login(
        payload: PlatformAdminLogin,
        session: Annotated[AsyncSession, Depends(platform_db)],
    ) -> PlatformAdminTokenResponse:
        secret = os.getenv("TENANCY_JWT_SECRET")
        if secret is None or len(secret) < 32:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Platform authentication is not configured",
            )

        result = await session.execute(
            select(PlatformAdmin).where(PlatformAdmin.email == payload.email)
        )
        admin = result.scalar_one_or_none()
        stored_hash = admin.password_hash if admin is not None else DUMMY_PASSWORD_HASH
        password_matches = password_hasher.verify(payload.password, stored_hash)
        if admin is None or not admin.is_active or not password_matches:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        now = datetime.now(UTC)
        lifetime = timedelta(minutes=30)
        token = jwt.encode(
            {
                "sub": admin.id,
                "email": admin.email,
                "iss": PLATFORM_ADMIN_JWT_ISSUER,
                "aud": PLATFORM_ADMIN_JWT_AUDIENCE,
                "iat": now,
                "exp": now + lifetime,
            },
            secret,
            algorithm=JWT_ALGORITHM,
        )
        await record_platform_audit(
            session,
            admin,
            action="sign_in",
            summary="Platform administrator signed in",
        )
        return PlatformAdminTokenResponse(
            access_token=token,
            expires_in=int(lifetime.total_seconds()),
            email=admin.email,
        )

    @app.get(
        "/api/v1/admin/tenants",
        response_model=TenantListResponse,
        tags=["Platform administration"],
    )
    async def list_tenants(
        request: Request,
        platform_admin: Annotated[PlatformAdmin, Depends(require_platform_admin)],
        skip: int = 0,
        limit: int = 50,
    ) -> TenantListResponse:
        if skip < 0 or limit < 1 or limit > 100:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Tenant pagination values are invalid",
            )

        manager: TenancyManager | None = request.app.state.tenancy_manager
        if manager is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Tenant administration is not configured",
            )
        try:
            tenants = await manager.store.list(skip=skip, limit=limit)
            total = await manager.store.count()
        except TenancyError as error:
            logger.exception("Tenant listing failed")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Tenant administration is temporarily unavailable",
            ) from error

        migrator = TenantMigrationManager(
            manager.config,
            manager.store,
            alembic_cfg_path=Path(__file__).resolve().parents[1] / "alembic.ini",
        )
        try:
            head_revision = ScriptDirectory.from_config(
                AlembicConfig(
                    Path(__file__).resolve().parents[1] / "alembic.ini",
                )
            ).get_current_head()
        except (CommandError, OSError, ValueError) as error:
            logger.exception("Could not determine the tenant migration head")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Tenant migration status is temporarily unavailable",
            ) from error

        migration_slots = asyncio.Semaphore(5)

        async def summarize_tenant(tenant: Tenant) -> TenantSummary:
            try:
                async with migration_slots:
                    current_revision = await asyncio.to_thread(
                        read_tenant_current_revision,
                        migrator,
                        tenant,
                    )
            except Exception:
                logger.exception(
                    "Could not read migration status for tenant %s",
                    tenant.identifier,
                )
                migration_status: Literal[
                    "current", "behind", "not_migrated", "unknown"
                ] = "unknown"
                current_revision = None
            else:
                migration_status = (
                    "not_migrated"
                    if current_revision is None
                    else "current"
                    if current_revision == head_revision
                    else "behind"
                )
            return TenantSummary(
                identifier=tenant.identifier,
                name=tenant.name,
                status=tenant.status.value,
                migration_status=migration_status,
                current_revision=current_revision,
                head_revision=head_revision,
            )

        summaries = await asyncio.gather(
            *(summarize_tenant(tenant) for tenant in tenants),
        )
        return TenantListResponse(
            items=summaries,
            total=total,
        )

    @app.get(
        "/api/v1/admin/activity",
        response_model=PlatformAuditEventListResponse,
        tags=["Platform administration"],
    )
    async def list_platform_activity(
        session: Annotated[AsyncSession, Depends(platform_db)],
        platform_admin: Annotated[PlatformAdmin, Depends(require_platform_admin)],
        limit: int = 50,
    ) -> PlatformAuditEventListResponse:
        if limit < 1 or limit > 100:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Activity pagination value is invalid",
            )
        try:
            result = await session.execute(
                select(PlatformAuditEvent)
                .order_by(PlatformAuditEvent.occurred_at.desc())
                .limit(limit)
            )
        except SQLAlchemyError as error:
            logger.exception("Platform activity listing failed")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Platform activity is temporarily unavailable",
            ) from error
        return PlatformAuditEventListResponse(
            items=[
                PlatformAuditEventResponse(
                    id=event.id,
                    admin_email=event.admin_email,
                    action=event.action,
                    tenant_identifier=event.tenant_identifier,
                    summary=event.summary,
                    occurred_at=event.occurred_at,
                )
                for event in result.scalars().all()
            ]
        )

    @app.post(
        "/api/v1/admin/tenants",
        response_model=TenantRegistrationResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Platform administration"],
    )
    async def register_tenant(
        payload: TenantRegistration,
        request: Request,
        platform_admin: Annotated[PlatformAdmin, Depends(require_platform_admin)],
        session: Annotated[AsyncSession, Depends(platform_db)],
    ) -> TenantRegistrationResponse:
        manager: TenancyManager | None = request.app.state.tenancy_manager
        if manager is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Tenant registration is not configured",
            )

        try:
            tenant = await manager.register_tenant(
                identifier=payload.identifier,
                name=payload.name,
                app_metadata=TenantBase.metadata,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Tenant could not be registered",
            ) from error
        except TenancyError as error:
            logger.exception("Tenant provisioning failed")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Tenant provisioning is temporarily unavailable",
            ) from error

        await record_platform_audit(
            session,
            platform_admin,
            action="tenant_created",
            tenant_identifier=tenant.identifier,
            summary=f"Created tenant {tenant.name} ({tenant.identifier})",
        )
        return TenantRegistrationResponse(
            identifier=tenant.identifier,
            name=tenant.name,
        )

    @app.patch(
        "/api/v1/admin/tenants/{identifier}/status",
        response_model=TenantStatusResponse,
        tags=["Platform administration"],
    )
    async def update_tenant_status(
        identifier: str,
        payload: TenantStatusUpdate,
        request: Request,
        platform_admin: Annotated[PlatformAdmin, Depends(require_platform_admin)],
        session: Annotated[AsyncSession, Depends(platform_db)],
    ) -> TenantStatusResponse:
        manager: TenancyManager | None = request.app.state.tenancy_manager
        if manager is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Tenant administration is not configured",
            )
        try:
            tenant = await manager.store.get_by_identifier(identifier)
            updated_tenant = await manager.set_status(
                tenant.id,
                TenantStatus(payload.status),
            )
        except TenantNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant not found",
            ) from error
        except TenancyError as error:
            logger.exception("Tenant status update failed")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Tenant administration is temporarily unavailable",
            ) from error
        await record_platform_audit(
            session,
            platform_admin,
            action=f"tenant_{updated_tenant.status.value}",
            tenant_identifier=updated_tenant.identifier,
            summary=f"Set tenant {updated_tenant.identifier} to {updated_tenant.status.value}",
        )
        return TenantStatusResponse(
            identifier=updated_tenant.identifier,
            name=updated_tenant.name,
            status=updated_tenant.status.value,
        )

    @app.post(
        "/api/v1/admin/tenants/{identifier}/migrations",
        response_model=ActionMessage,
        tags=["Platform administration"],
    )
    async def migrate_tenant(
        identifier: str,
        request: Request,
        platform_admin: Annotated[PlatformAdmin, Depends(require_platform_admin)],
        session: Annotated[AsyncSession, Depends(platform_db)],
    ) -> ActionMessage:
        manager: TenancyManager | None = request.app.state.tenancy_manager
        if manager is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Tenant administration is not configured",
            )
        try:
            tenant = await manager.store.get_by_identifier(identifier)
        except TenantNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant not found",
            ) from error
        except TenancyError as error:
            logger.exception("Tenant lookup failed before migration")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Tenant administration is temporarily unavailable",
            ) from error

        migrator = TenantMigrationManager(
            manager.config,
            manager.store,
            alembic_cfg_path=Path(__file__).resolve().parents[1] / "alembic.ini",
        )
        try:
            await migrator.upgrade_tenant(tenant, revision="head")
        except MigrationError as error:
            logger.exception("Tenant migration failed for %s", identifier)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Tenant migration failed; inspect backend logs for details",
            ) from error
        await record_platform_audit(
            session,
            platform_admin,
            action="tenant_migrated",
            tenant_identifier=tenant.identifier,
            summary=f"Ran tenant migrations for {tenant.identifier}",
        )
        return ActionMessage(message="Tenant database is up to date.")

    @app.post(
        "/api/v1/admin/tenants/{identifier}/admin-invitations",
        response_model=ActionMessage,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["Platform administration"],
    )
    async def invite_tenant_admin(
        identifier: str,
        payload: TenantAdminProvision,
        request: Request,
        platform_admin: Annotated[PlatformAdmin, Depends(require_platform_admin)],
        platform_session: Annotated[AsyncSession, Depends(platform_db)],
    ) -> ActionMessage:
        require_email_configuration()

        current_manager: TenancyManager | None = request.app.state.tenancy_manager
        if current_manager is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Tenant administration is not configured",
            )

        try:
            tenant = await current_manager.store.get_by_identifier(identifier)
        except TenantNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant not found",
            ) from error
        except TenancyError as error:
            logger.exception("Tenant lookup failed during administrator provisioning")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Tenant administration is temporarily unavailable",
            ) from error
        if tenant.status != TenantStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The school tenant must be active before inviting an administrator",
            )

        invitation_token = make_action_token()
        try:
            invitation_link = action_url(
                tenant.identifier,
                "/invitation/accept",
                invitation_token,
            )
        except ValueError as error:
            logger.error("Invalid public app URL for school invitation: %s", error)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Invitation links are not configured correctly",
            ) from error

        user_id: str
        try:
            async with current_manager.isolation_provider.get_session(tenant) as session:
                existing_user = await find_tenant_user(
                    session,
                    email=payload.email,
                )
                if existing_user is not None and existing_user.is_active:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="An administrator with this email already exists",
                    )

                if existing_user is None:
                    user = TenantUser(
                        id=uuid.uuid4().hex,
                        email=payload.email,
                        password_hash=DUMMY_PASSWORD_HASH,
                        role="school_admin",
                        is_active=False,
                    )
                    await session.execute(
                        insert(tenant_users_table(session)).values(
                            id=user.id,
                            email=user.email,
                            password_hash=user.password_hash,
                            role=user.role,
                            is_active=user.is_active,
                        )
                    )
                    user_id = user.id
                else:
                    user_id = existing_user.id

                await store_action_token(
                    session,
                    user_id=user_id,
                    purpose="invitation",
                    token=invitation_token,
                    expires_at=datetime.now(UTC) + timedelta(hours=48),
                )
                await session.commit()
        except IntegrityError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An administrator with this email already exists",
            ) from error
        except TenancyError as error:
            logger.exception("Tenant administrator provisioning failed")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Tenant administrator provisioning is temporarily unavailable",
            ) from error

        try:
            await deliver_action_email(
                payload.email,
                "Your Northstar school administrator invitation",
                (
                    f"You have been invited to administer {tenant.name}.\n\n"
                    f"Accept your invitation within 48 hours:\n{invitation_link}\n\n"
                    "If you were not expecting this invitation, you can ignore this message."
                ),
            )
        except (OSError, smtplib.SMTPException):
            logger.exception("Failed to deliver school administrator invitation")
            try:
                async with current_manager.isolation_provider.get_session(tenant) as session:
                    await revoke_action_token(
                        session,
                        token=invitation_token,
                        purpose="invitation",
                    )
            except TenancyError:
                logger.exception("Failed to revoke undelivered school invitation")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Invitation email could not be delivered",
            )

        await record_platform_audit(
            platform_session,
            platform_admin,
            action="tenant_admin_invited",
            tenant_identifier=tenant.identifier,
            summary=f"Invited {payload.email} to administer {tenant.identifier}",
        )
        return ActionMessage(message="Invitation email sent")

    @app.post(
        "/api/v1/auth/invitations/accept",
        response_model=ActionMessage,
        tags=["Authentication"],
    )
    async def accept_school_invitation(
        payload: TokenPasswordSet,
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> ActionMessage:
        invitation_roles = {
            "invitation": "school_admin",
            "teacher_invitation": "teacher",
            "guardian_invitation": "guardian",
        }
        invitation_purpose = "invitation"
        user_id = None
        for purpose in invitation_roles:
            user_id = await consume_action_token(
                session,
                token=payload.token,
                purpose=purpose,
            )
            if user_id is not None:
                invitation_purpose = purpose
                break
        if user_id is None:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invitation link is invalid or expired",
            )

        table = tenant_users_table(session)
        result = await session.execute(
            update(table)
            .where(table.c.id == user_id, table.c.is_active.is_(False))
            .values(
                password_hash=password_hasher.hash(payload.password),
                is_active=True,
                role=invitation_roles[invitation_purpose],
            )
        )
        if result.rowcount != 1:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invitation link is invalid or expired",
            )
        await session.commit()
        return ActionMessage(message="Invitation accepted. You can now sign in.")

    @app.post(
        "/api/v1/auth/password-reset/request",
        response_model=ActionMessage,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["Authentication"],
    )
    async def request_password_reset(
        payload: PasswordResetRequest,
        tenant: Annotated[Tenant, Depends(get_request_tenant)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> ActionMessage:
        require_email_configuration()

        user = await find_tenant_user(session, email=payload.email)
        if user is not None and user.is_active:
            reset_token = make_action_token()
            try:
                reset_link = action_url(
                    tenant.identifier,
                    "/password/reset",
                    reset_token,
                )
            except ValueError as error:
                logger.error("Invalid public app URL for password reset: %s", error)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Password reset links are not configured correctly",
                ) from error

            await store_action_token(
                session,
                user_id=user.id,
                purpose="password_reset",
                token=reset_token,
                expires_at=datetime.now(UTC) + timedelta(minutes=30),
            )
            await session.commit()

            try:
                await deliver_action_email(
                    user.email,
                    "Reset your Northstar password",
                    (
                        f"Use this link to reset your Northstar password within 30 minutes:\n"
                        f"{reset_link}\n\n"
                        "If you did not request a password reset, you can ignore this message."
                    ),
                )
            except (OSError, smtplib.SMTPException):
                logger.exception("Failed to deliver password reset email")
                try:
                    await revoke_action_token(
                        session,
                        token=reset_token,
                        purpose="password_reset",
                    )
                except TenancyError:
                    logger.exception("Failed to revoke undelivered password reset token")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Password reset email could not be delivered",
                )

        return ActionMessage(
            message="If the account exists, password reset instructions will be sent."
        )

    @app.post(
        "/api/v1/auth/password-reset/confirm",
        response_model=ActionMessage,
        tags=["Authentication"],
    )
    async def confirm_password_reset(
        payload: TokenPasswordSet,
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> ActionMessage:
        user_id = await consume_action_token(
            session,
            token=payload.token,
            purpose="password_reset",
        )
        if user_id is None:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password reset link is invalid or expired",
            )

        table = tenant_users_table(session)
        result = await session.execute(
            update(table)
            .where(table.c.id == user_id, table.c.is_active.is_(True))
            .values(password_hash=password_hasher.hash(payload.password))
        )
        if result.rowcount != 1:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password reset link is invalid or expired",
            )

        token_table = tenant_action_tokens_table(session)
        await session.execute(
            update(token_table)
            .where(
                token_table.c.user_id == user_id,
                token_table.c.purpose == "password_reset",
                token_table.c.used_at.is_(None),
            )
            .values(used_at=datetime.now(UTC))
        )
        await session.commit()
        return ActionMessage(message="Password reset. You can now sign in.")

    @app.post(
        "/api/v1/auth/login",
        response_model=TokenResponse,
        tags=["Authentication"],
    )
    async def login(
        payload: SchoolLogin,
        tenant: Annotated[Tenant, Depends(get_request_tenant)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> TokenResponse:
        secret = os.getenv("TENANCY_JWT_SECRET")
        if secret is None or len(secret) < 32:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="School authentication is not configured",
            )

        user = await find_tenant_user(session, email=payload.email)
        stored_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
        password_matches = password_hasher.verify(payload.password, stored_hash)
        if user is None or not user.is_active or not password_matches:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        now = datetime.now(UTC)
        token_lifetime = timedelta(minutes=30)
        token = jwt.encode(
            {
                "sub": user.id,
                "tenant_id": tenant.identifier,
                "role": user.role,
                "iss": JWT_ISSUER,
                "iat": now,
                "exp": now + token_lifetime,
            },
            secret,
            algorithm=JWT_ALGORITHM,
        )
        return TokenResponse(
            access_token=token,
            expires_in=int(token_lifetime.total_seconds()),
            role=user.role,
        )

    async def require_school_user(
        tenant: Annotated[Tenant, Depends(get_request_tenant)],
        authorization: Annotated[str | None, Header()] = None,
        session: AsyncSession = Depends(tenant_db),
    ) -> TenantUser:
        secret = os.getenv("TENANCY_JWT_SECRET")
        if secret is None or len(secret) < 32:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="School authentication is not configured",
            )
        if authorization is None or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )

        try:
            claims = jwt.decode(
                authorization[7:],
                secret,
                algorithms=[JWT_ALGORITHM],
                issuer=JWT_ISSUER,
                options={"require": ["sub", "tenant_id", "role", "exp", "iss"]},
            )
        except jwt.InvalidTokenError as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired access token",
                headers={"WWW-Authenticate": "Bearer"},
            ) from error

        if claims["tenant_id"] != tenant.identifier:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant mismatch",
            )

        user = await find_tenant_user(session, user_id=claims["sub"])
        if user is None or not user.is_active or user.role != claims["role"]:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or inactive account",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user

    async def require_school_admin(
        user: Annotated[TenantUser, Depends(require_school_user)],
    ) -> TenantUser:
        if user.role != "school_admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="School administrator access required",
            )
        return user

    async def require_guardian(
        user: Annotated[TenantUser, Depends(require_school_user)],
    ) -> TenantUser:
        if user.role != "guardian":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Guardian access required",
            )
        return user

    async def require_class_access(
        session: AsyncSession,
        user: TenantUser,
        class_section_id: str,
    ) -> None:
        if user.role == "school_admin":
            return
        if user.role != "teacher":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Class access is not available for this account",
            )
        assignment_table = tenant_teacher_assignment_table(session)
        result = await session.execute(
            select(assignment_table.c.id).where(
                assignment_table.c.teacher_user_id == user.id,
                assignment_table.c.class_section_id == class_section_id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not assigned to this class",
            )

    async def billing_settings_for(
        session: AsyncSession,
    ) -> tuple[Table, Mapping[str, Any] | None]:
        settings_table = tenant_billing_tables(session)[0]
        result = await session.execute(select(settings_table).limit(1))
        return settings_table, result.mappings().one_or_none()

    def billing_settings_response(
        row: Mapping[str, Any] | None,
    ) -> BillingSettingsResponse:
        provider = row["provider"] if row is not None else "manual"
        currency = row["currency"] if row is not None else "NGN"
        credentials = decrypt_billing_credentials(
            row["credentials_encrypted"] if row is not None else None
        )
        selected = credentials.get(provider, {})
        has_secret = bool(selected.get("secret_key"))
        has_webhook = bool(selected.get("webhook_secret"))
        return BillingSettingsResponse(
            provider=provider,
            currency=currency,
            public_key=row["public_key"] if row is not None else None,
            has_secret_key=has_secret,
            has_webhook_secret=has_webhook,
            configured=(
                provider == "manual"
                or (provider == "paystack" and has_secret)
                or (provider == "stripe" and has_secret and has_webhook)
            ),
        )

    async def invoice_responses(
        session: AsyncSession,
        invoice_rows: Sequence[Mapping[str, Any]],
    ) -> list[InvoiceResponse]:
        if not invoice_rows:
            return []
        payment_table = tenant_billing_tables(session)[3]
        invoice_ids = [row["id"] for row in invoice_rows]
        payment_result = await session.execute(
            select(payment_table).where(
                payment_table.c.invoice_id.in_(invoice_ids),
            ).order_by(payment_table.c.paid_at, payment_table.c.created_at)
        )
        payments_by_invoice: dict[str, list[PaymentResponse]] = {
            invoice_id: [] for invoice_id in invoice_ids
        }
        for payment in payment_result.mappings():
            payments_by_invoice[payment["invoice_id"]].append(
                PaymentResponse(
                    id=payment["id"],
                    amount=payment["amount"],
                    method=payment["method"],
                    reference=payment["reference"],
                    paid_at=payment["paid_at"],
                    voided_at=payment["voided_at"],
                )
            )

        responses = []
        for row in invoice_rows:
            payments = payments_by_invoice[row["id"]]
            paid_amount = sum(
                (payment.amount for payment in payments if payment.voided_at is None),
                Decimal("0"),
            )
            line_items = json.loads(row["line_items"])
            responses.append(
                InvoiceResponse(
                    id=row["id"],
                    invoice_number=row["invoice_number"],
                    student_id=row["student_id"],
                    student_name=f'{row["first_name"]} {row["last_name"]}',
                    admission_number=row["admission_number"],
                    class_section_id=row["class_section_id"],
                    class_name=row["class_name"],
                    term_id=row["term_id"],
                    term_name=row["term_name"],
                    amount=row["amount"],
                    currency=row["currency"],
                    balance=max(row["amount"] - paid_amount, Decimal("0")),
                    status=(
                        "paid"
                        if paid_amount >= row["amount"]
                        else "overdue"
                        if row["due_date"] < datetime.now(UTC).date()
                        else "issued"
                    ),
                    issued_at=row["issued_at"],
                    due_date=row["due_date"],
                    line_items=[
                        InvoiceLineItemResponse(**line_item)
                        for line_item in line_items
                    ],
                    payments=payments,
                )
            )
        return responses

    def invoice_list_statement(
        session: AsyncSession,
        invoice_table: Table | None = None,
        guardian_email: str | None = None,
    ):
        student_table, _, class_table, enrollment_table = tenant_academic_tables(session)
        if invoice_table is None:
            invoice_table = tenant_billing_tables(session)[2]
        term_table = tenant_academic_term_table(session)
        query = (
            select(
                invoice_table,
                student_table.c.id.label("student_id"),
                student_table.c.first_name,
                student_table.c.last_name,
                student_table.c.admission_number,
                student_table.c.guardian_email,
                class_table.c.id.label("class_section_id"),
                class_table.c.name.label("class_name"),
                term_table.c.name.label("term_name"),
            )
            .select_from(
                invoice_table.join(
                    enrollment_table,
                    invoice_table.c.enrollment_id == enrollment_table.c.id,
                )
                .join(student_table, enrollment_table.c.student_id == student_table.c.id)
                .join(class_table, enrollment_table.c.class_section_id == class_table.c.id)
                .join(term_table, invoice_table.c.term_id == term_table.c.id)
            )
            .where(invoice_table.c.voided_at.is_(None))
        )
        if guardian_email is not None:
            query = query.where(
                func.lower(student_table.c.guardian_email) == guardian_email.lower()
            )
        return query

    @app.get(
        "/api/v1/billing/settings",
        response_model=BillingSettingsResponse,
        tags=["Billing"],
    )
    async def get_billing_settings(
        _user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> BillingSettingsResponse:
        _table, row = await billing_settings_for(session)
        return billing_settings_response(row)

    @app.put(
        "/api/v1/billing/settings",
        response_model=BillingSettingsResponse,
        tags=["Billing"],
    )
    async def update_billing_settings(
        payload: BillingSettingsUpdate,
        user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> BillingSettingsResponse:
        settings_table, row = await billing_settings_for(session)
        stored = decrypt_billing_credentials(
            row["credentials_encrypted"] if row is not None else None
        )
        old_provider = row["provider"] if row is not None else None
        if payload.clear_credentials:
            stored.pop(payload.provider, None)
        else:
            provider_credentials = stored.setdefault(payload.provider, {})
            if payload.secret_key is not None:
                provider_credentials["secret_key"] = payload.secret_key
            if payload.webhook_secret is not None:
                provider_credentials["webhook_secret"] = payload.webhook_secret
        encrypted = encrypt_billing_credentials(stored) if stored else None
        if payload.clear_credentials:
            public_key = None
        elif payload.public_key is not None:
            public_key = payload.public_key
        elif old_provider not in (None, payload.provider):
            public_key = None
        else:
            public_key = row["public_key"] if row is not None else None
        values = {
            "provider": payload.provider,
            "currency": payload.currency.upper(),
            "public_key": public_key,
            "credentials_encrypted": encrypted,
            "updated_by_user_id": user.id,
            "updated_at": datetime.now(UTC),
        }
        if row is None:
            try:
                await session.execute(
                    insert(settings_table).values(id="default", **values)
                )
            except IntegrityError as error:
                await session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Billing settings changed concurrently; reload and retry",
                ) from error
        else:
            await session.execute(
                update(settings_table).where(settings_table.c.id == row["id"]).values(**values)
            )
        await session.commit()
        _table, updated_row = await billing_settings_for(session)
        return billing_settings_response(updated_row)

    @app.get(
        "/api/v1/billing/fees",
        response_model=list[FeeStructureResponse],
        tags=["Billing"],
    )
    async def list_fee_structures(
        _user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> list[FeeStructureResponse]:
        _student, year_table, class_table, _enrollment = tenant_academic_tables(session)
        fee_table = tenant_billing_tables(session)[1]
        term_table = tenant_academic_term_table(session)
        result = await session.execute(
            select(
                fee_table,
                class_table.c.name.label("class_name"),
                year_table.c.name.label("academic_year"),
                term_table.c.name.label("term_name"),
            )
            .select_from(
                fee_table.join(class_table, fee_table.c.class_section_id == class_table.c.id)
                .join(year_table, class_table.c.academic_year_id == year_table.c.id)
                .join(term_table, fee_table.c.term_id == term_table.c.id)
            )
            .order_by(year_table.c.name, class_table.c.name, term_table.c.start_date)
        )
        settings_table, settings = await billing_settings_for(session)
        del settings_table
        currency = settings["currency"] if settings is not None else "NGN"
        return [
            FeeStructureResponse(
                id=row["id"],
                class_section_id=row["class_section_id"],
                class_name=row["class_name"],
                academic_year=row["academic_year"],
                term_id=row["term_id"],
                term_name=row["term_name"],
                description=row["description"],
                amount=row["amount"],
                currency=currency,
                is_active=row["is_active"],
            )
            for row in result.mappings()
        ]

    @app.post(
        "/api/v1/billing/fees",
        response_model=FeeStructureResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Billing"],
    )
    async def create_fee_structure(
        payload: FeeStructureCreate,
        user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> FeeStructureResponse:
        _student, year_table, class_table, _enrollment = tenant_academic_tables(session)
        fee_table = tenant_billing_tables(session)[1]
        term_table = tenant_academic_term_table(session)
        result = await session.execute(
            select(
                class_table.c.name.label("class_name"),
                class_table.c.academic_year_id,
                year_table.c.name.label("academic_year"),
                term_table.c.name.label("term_name"),
                term_table.c.academic_year_id.label("term_year_id"),
            )
            .select_from(
                class_table.join(year_table, class_table.c.academic_year_id == year_table.c.id)
                .join(term_table, term_table.c.id == payload.term_id)
            )
            .where(class_table.c.id == payload.class_section_id)
        )
        class_term = result.mappings().one_or_none()
        if class_term is None or class_term["academic_year_id"] != class_term["term_year_id"]:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="The selected term must belong to the selected class's academic year",
            )
        fee_id = uuid.uuid4().hex
        try:
            await session.execute(
                insert(fee_table).values(
                    id=fee_id,
                    class_section_id=payload.class_section_id,
                    term_id=payload.term_id,
                    description=payload.description,
                    amount=payload.amount,
                    is_active=True,
                    created_by_user_id=user.id,
                    created_at=datetime.now(UTC),
                )
            )
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A fee with this description already exists for the class and term",
            ) from error
        settings_table, settings = await billing_settings_for(session)
        del settings_table
        return FeeStructureResponse(
            id=fee_id,
            class_section_id=payload.class_section_id,
            class_name=class_term["class_name"],
            academic_year=class_term["academic_year"],
            term_id=payload.term_id,
            term_name=class_term["term_name"],
            description=payload.description,
            amount=payload.amount,
            currency=settings["currency"] if settings is not None else "NGN",
            is_active=True,
        )

    @app.patch(
        "/api/v1/billing/fees/{fee_id}",
        response_model=FeeStructureResponse,
        tags=["Billing"],
    )
    async def update_fee_structure_status(
        fee_id: str,
        payload: FeeStructureStatusUpdate,
        _user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> FeeStructureResponse:
        _student, year_table, class_table, _enrollment = tenant_academic_tables(session)
        fee_table = tenant_billing_tables(session)[1]
        term_table = tenant_academic_term_table(session)
        result = await session.execute(
            select(
                fee_table,
                class_table.c.name.label("class_name"),
                year_table.c.name.label("academic_year"),
                term_table.c.name.label("term_name"),
            )
            .select_from(
                fee_table.join(class_table, fee_table.c.class_section_id == class_table.c.id)
                .join(year_table, class_table.c.academic_year_id == year_table.c.id)
                .join(term_table, fee_table.c.term_id == term_table.c.id)
            )
            .where(fee_table.c.id == fee_id)
        )
        row = result.mappings().one_or_none()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fee structure not found")
        await session.execute(
            update(fee_table).where(fee_table.c.id == fee_id).values(is_active=payload.is_active)
        )
        await session.commit()
        settings_table, settings = await billing_settings_for(session)
        del settings_table
        return FeeStructureResponse(
            id=fee_id,
            class_section_id=row["class_section_id"],
            class_name=row["class_name"],
            academic_year=row["academic_year"],
            term_id=row["term_id"],
            term_name=row["term_name"],
            description=row["description"],
            amount=row["amount"],
            currency=settings["currency"] if settings is not None else "NGN",
            is_active=payload.is_active,
        )

    @app.post(
        "/api/v1/billing/invoices/generate",
        response_model=InvoiceGenerateResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Billing"],
    )
    async def generate_class_invoices(
        payload: InvoiceGenerateRequest,
        _user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> InvoiceGenerateResponse:
        student_table, _year_table, class_table, enrollment_table = tenant_academic_tables(session)
        fee_table, invoice_table = tenant_billing_tables(session)[1:3]
        term_table = tenant_academic_term_table(session)
        class_result = await session.execute(
            select(
                class_table.c.academic_year_id.label("class_year_id"),
                term_table.c.academic_year_id.label("term_year_id"),
            )
            .select_from(class_table.join(term_table, term_table.c.id == payload.term_id))
            .where(class_table.c.id == payload.class_section_id)
        )
        class_term = class_result.one_or_none()
        if class_term is None or class_term.class_year_id != class_term.term_year_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="The selected term must belong to the selected class's academic year",
            )
        fee_result = await session.execute(
            select(fee_table.c.description, fee_table.c.amount).where(
                fee_table.c.class_section_id == payload.class_section_id,
                fee_table.c.term_id == payload.term_id,
                fee_table.c.is_active.is_(True),
            )
        )
        line_items = [
            {"description": row.description, "amount": str(row.amount)}
            for row in fee_result
        ]
        if not line_items:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Add at least one active fee structure before generating invoices",
            )
        amount = sum((Decimal(item["amount"]) for item in line_items), Decimal("0"))
        settings_table, settings = await billing_settings_for(session)
        del settings_table
        currency = settings["currency"] if settings is not None else "NGN"
        enrollments_result = await session.execute(
            select(
                enrollment_table.c.id.label("enrollment_id"),
                enrollment_table.c.student_id,
            )
            .select_from(
                enrollment_table.join(student_table, enrollment_table.c.student_id == student_table.c.id)
            )
            .where(
                enrollment_table.c.class_section_id == payload.class_section_id,
                enrollment_table.c.status == "active",
                student_table.c.is_active.is_(True),
            )
        )
        enrollment_rows = enrollments_result.mappings().all()
        existing_result = await session.execute(
            select(invoice_table.c.enrollment_id).where(
                invoice_table.c.term_id == payload.term_id,
                invoice_table.c.voided_at.is_(None),
            )
        )
        existing_enrollment_ids = set(existing_result.scalars().all())
        issued_at = datetime.now(UTC).date()
        created = 0
        try:
            for enrollment in enrollment_rows:
                if enrollment["enrollment_id"] in existing_enrollment_ids:
                    continue
                await session.execute(
                    insert(invoice_table).values(
                        id=uuid.uuid4().hex,
                        invoice_number=f"INV-{issued_at:%Y%m%d}-{uuid.uuid4().hex[:8].upper()}",
                        enrollment_id=enrollment["enrollment_id"],
                        term_id=payload.term_id,
                        amount=amount,
                        currency=currency,
                        line_items=json.dumps(line_items),
                        status="issued",
                        issued_at=issued_at,
                        due_date=payload.due_date,
                        created_at=datetime.now(UTC),
                    )
                )
                created += 1
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Invoice generation conflicted with another request; refresh and retry",
            ) from error
        invoice_query = invoice_list_statement(session, invoice_table).where(
            invoice_table.c.term_id == payload.term_id,
            invoice_table.c.enrollment_id.in_(
                [row["enrollment_id"] for row in enrollment_rows]
            ),
        )
        result = await session.execute(invoice_query.order_by(invoice_table.c.invoice_number))
        invoice_items = await invoice_responses(session, result.mappings().all())
        return InvoiceGenerateResponse(
            created=created,
            skipped=len(enrollment_rows) - created,
            invoices=invoice_items,
        )

    @app.get(
        "/api/v1/billing/invoices",
        response_model=InvoiceListResponse,
        tags=["Billing"],
    )
    async def list_billing_invoices(
        _user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> InvoiceListResponse:
        invoice_table = tenant_billing_tables(session)[2]
        query = invoice_list_statement(session, invoice_table)
        count_result = await session.execute(
            select(func.count()).select_from(invoice_table).where(invoice_table.c.voided_at.is_(None))
        )
        total = count_result.scalar_one()
        result = await session.execute(query.order_by(invoice_table.c.issued_at.desc()))
        return InvoiceListResponse(
            items=await invoice_responses(session, result.mappings().all()),
            total=total,
        )

    @app.get(
        "/api/v1/analytics/dashboard",
        response_model=AnalyticsDashboardResponse,
        tags=["Analytics"],
    )
    async def get_analytics_dashboard(
        _user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> AnalyticsDashboardResponse:
        today = date.today()
        month_keys: list[tuple[int, int, str]] = []
        for offset in range(11, -1, -1):
            month_index = today.year * 12 + today.month - 1 - offset
            year, month_zero_based = divmod(month_index, 12)
            month = month_zero_based + 1
            month_keys.append((year, month, f"{year:04d}-{month:02d}"))
        start_date = date(month_keys[0][0], month_keys[0][1], 1)

        student_table, _year_table, class_table, enrollment_table = tenant_academic_tables(session)
        attendance_table = tenant_attendance_table(session)
        assessment_table, grade_table = tenant_assessment_tables(session)
        _settings_table, _fee_table, invoice_table, payment_table = tenant_billing_tables(session)

        active_students_result = await session.execute(
            select(func.count(student_table.c.id)).where(student_table.c.is_active.is_(True))
        )
        active_enrollments_result = await session.execute(
            select(func.count(enrollment_table.c.id))
            .select_from(
                enrollment_table.join(
                    student_table,
                    enrollment_table.c.student_id == student_table.c.id,
                )
            )
            .where(
                enrollment_table.c.status == "active",
                student_table.c.is_active.is_(True),
            )
        )
        active_classes_result = await session.execute(
            select(func.count(class_table.c.id)).where(class_table.c.is_active.is_(True))
        )

        enrollment_year = func.extract("year", enrollment_table.c.enrolled_at)
        enrollment_month = func.extract("month", enrollment_table.c.enrolled_at)
        enrollment_result = await session.execute(
            select(
                enrollment_year.label("year"),
                enrollment_month.label("month"),
                func.count(enrollment_table.c.id).label("enrollments"),
            )
            .select_from(
                enrollment_table.join(
                    student_table,
                    enrollment_table.c.student_id == student_table.c.id,
                )
            )
            .where(
                enrollment_table.c.status == "active",
                student_table.c.is_active.is_(True),
                enrollment_table.c.enrolled_at >= start_date,
                enrollment_table.c.enrolled_at <= today,
            )
            .group_by(enrollment_year, enrollment_month)
        )
        enrollment_counts = {
            (int(row["year"]), int(row["month"])): int(row["enrollments"])
            for row in enrollment_result.mappings()
        }
        enrollment_trend = [
            AnalyticsEnrollmentPoint(
                month=month_key,
                enrollments=enrollment_counts.get((year, month), 0),
            )
            for year, month, month_key in month_keys
        ]

        attendance_year = func.extract("year", attendance_table.c.attendance_date)
        attendance_month = func.extract("month", attendance_table.c.attendance_date)
        attendance_status_counts = {
            attendance_status: func.coalesce(
                func.sum(case((attendance_table.c.status == attendance_status, 1), else_=0)),
                0,
            )
            for attendance_status in ("present", "late", "absent", "excused")
        }
        attendance_result = await session.execute(
            select(
                attendance_year.label("year"),
                attendance_month.label("month"),
                *[
                    attendance_status_counts[attendance_status].label(attendance_status)
                    for attendance_status in attendance_status_counts
                ],
            )
            .where(
                attendance_table.c.attendance_date >= start_date,
                attendance_table.c.attendance_date <= today,
            )
            .group_by(attendance_year, attendance_month)
        )
        attendance_by_month: dict[tuple[int, int], dict[str, int]] = {}
        attendance_totals = {
            attendance_status: 0
            for attendance_status in attendance_status_counts
        }
        for row in attendance_result.mappings():
            counts = {
                attendance_status: int(row[attendance_status] or 0)
                for attendance_status in attendance_status_counts
            }
            attendance_by_month[(int(row["year"]), int(row["month"]))] = counts
            for attendance_status, count in counts.items():
                attendance_totals[attendance_status] += count

        def attendance_rate(counts: Mapping[str, int]) -> Decimal | None:
            eligible_count = counts["present"] + counts["late"] + counts["absent"]
            if eligible_count == 0:
                return None
            return (
                Decimal(counts["present"] + counts["late"])
                / Decimal(eligible_count)
                * Decimal("100")
            )

        attendance_trend = []
        for year, month, month_key in month_keys:
            counts = attendance_by_month.get(
                (year, month),
                {"present": 0, "late": 0, "absent": 0, "excused": 0},
            )
            attendance_trend.append(
                AnalyticsAttendancePoint(
                    month=month_key,
                    rate=attendance_rate(counts),
                    marked_count=sum(counts.values()),
                )
            )

        paid_by_invoice = (
            select(
                payment_table.c.invoice_id.label("invoice_id"),
                func.sum(payment_table.c.amount).label("paid_amount"),
            )
            .where(payment_table.c.voided_at.is_(None))
            .group_by(payment_table.c.invoice_id)
            .subquery()
        )
        paid_amount = func.coalesce(paid_by_invoice.c.paid_amount, Decimal("0"))
        raw_balance = invoice_table.c.amount - paid_amount
        outstanding_balance = case((raw_balance > 0, raw_balance), else_=Decimal("0"))
        fee_result = await session.execute(
            select(
                invoice_table.c.currency,
                func.coalesce(func.sum(invoice_table.c.amount), 0).label("total_invoiced"),
                func.coalesce(func.sum(paid_amount), 0).label("total_collected"),
                func.coalesce(func.sum(outstanding_balance), 0).label("total_outstanding"),
                func.count(invoice_table.c.id).label("invoice_count"),
                func.coalesce(
                    func.sum(case((paid_amount >= invoice_table.c.amount, 1), else_=0)),
                    0,
                ).label("paid_invoice_count"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                and_(invoice_table.c.due_date < today, raw_balance > 0),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("overdue_invoice_count"),
            )
            .select_from(
                invoice_table.outerjoin(
                    paid_by_invoice,
                    invoice_table.c.id == paid_by_invoice.c.invoice_id,
                )
            )
            .where(invoice_table.c.voided_at.is_(None))
            .group_by(invoice_table.c.currency)
            .order_by(invoice_table.c.currency)
        )
        fee_summaries = []
        for row in fee_result.mappings():
            total_invoiced = Decimal(str(row["total_invoiced"]))
            total_collected = Decimal(str(row["total_collected"]))
            fee_summaries.append(
                BillingCurrencySummary(
                    currency=row["currency"],
                    total_invoiced=total_invoiced,
                    total_collected=total_collected,
                    total_outstanding=Decimal(str(row["total_outstanding"])),
                    collection_rate=(
                        total_collected / total_invoiced * Decimal("100")
                        if total_invoiced > 0
                        else Decimal("0")
                    ),
                    invoice_count=int(row["invoice_count"]),
                    paid_invoice_count=int(row["paid_invoice_count"]),
                    overdue_invoice_count=int(row["overdue_invoice_count"]),
                )
            )

        score_percentage = grade_table.c.score * 100 / assessment_table.c.max_score
        score_band = case(
            (score_percentage < 50, "0-49"),
            (score_percentage < 60, "50-59"),
            (score_percentage < 70, "60-69"),
            (score_percentage < 80, "70-79"),
            else_="80-100",
        )
        grade_result = await session.execute(
            select(
                score_band.label("band"),
                func.count(grade_table.c.id).label("count"),
            )
            .select_from(
                grade_table.join(
                    assessment_table,
                    grade_table.c.assessment_id == assessment_table.c.id,
                )
            )
            .group_by(score_band)
        )
        grade_counts = {row["band"]: int(row["count"]) for row in grade_result.mappings()}
        average_grade_result = await session.execute(
            select(
                func.avg(score_percentage),
                func.count(grade_table.c.id),
            )
            .select_from(
                grade_table.join(
                    assessment_table,
                    grade_table.c.assessment_id == assessment_table.c.id,
                )
            )
        )
        average_grade, graded_count = average_grade_result.one()
        grade_bands = ("0-49", "50-59", "60-69", "70-79", "80-100")

        return AnalyticsDashboardResponse(
            active_students=int(active_students_result.scalar_one()),
            active_enrollments=int(active_enrollments_result.scalar_one()),
            active_classes=int(active_classes_result.scalar_one()),
            enrollment_trend=enrollment_trend,
            attendance=AnalyticsAttendanceSummary(
                rate=attendance_rate(attendance_totals),
                marked_count=sum(attendance_totals.values()),
                present_count=attendance_totals["present"],
                late_count=attendance_totals["late"],
                absent_count=attendance_totals["absent"],
                excused_count=attendance_totals["excused"],
                trend=attendance_trend,
            ),
            fee_summaries=fee_summaries,
            grade_distribution=[
                GradeDistributionBand(band=band, count=grade_counts.get(band, 0))
                for band in grade_bands
            ],
            average_grade=Decimal(str(average_grade)) if average_grade is not None else None,
            graded_count=int(graded_count),
        )

    @app.get(
        "/api/v1/billing/dashboard",
        response_model=BillingDashboardResponse,
        tags=["Billing"],
    )
    async def get_billing_dashboard(
        _user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
        term_id: str | None = None,
        defaulters_limit: int = 20,
    ) -> BillingDashboardResponse:
        if not 1 <= defaulters_limit <= 100:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Defaulters limit must be between 1 and 100",
            )

        student_table, _, class_table, enrollment_table = tenant_academic_tables(session)
        term_table = tenant_academic_term_table(session)
        invoice_table, payment_table = tenant_billing_tables(session)[2:]
        paid_table = (
            select(
                payment_table.c.invoice_id.label("invoice_id"),
                func.sum(payment_table.c.amount).label("paid_amount"),
            )
            .where(payment_table.c.voided_at.is_(None))
            .group_by(payment_table.c.invoice_id)
            .subquery()
        )
        paid_amount = func.coalesce(paid_table.c.paid_amount, Decimal("0"))
        raw_balance = invoice_table.c.amount - paid_amount
        balance = case((raw_balance > 0, raw_balance), else_=Decimal("0"))
        overdue_balance = case(
            (and_(invoice_table.c.due_date < date.today(), raw_balance > 0), balance),
            else_=Decimal("0"),
        )
        paid_invoice = case((paid_amount >= invoice_table.c.amount, 1), else_=0)
        overdue_invoice = case((overdue_balance > 0, 1), else_=0)
        from_clause = (
            invoice_table.join(
                enrollment_table,
                invoice_table.c.enrollment_id == enrollment_table.c.id,
            )
            .join(student_table, enrollment_table.c.student_id == student_table.c.id)
            .join(class_table, enrollment_table.c.class_section_id == class_table.c.id)
            .join(term_table, invoice_table.c.term_id == term_table.c.id)
            .outerjoin(paid_table, invoice_table.c.id == paid_table.c.invoice_id)
        )
        filters = [invoice_table.c.voided_at.is_(None)]
        if term_id is not None:
            filters.append(invoice_table.c.term_id == term_id)

        summary_result = await session.execute(
            select(
                invoice_table.c.currency,
                func.coalesce(func.sum(invoice_table.c.amount), 0).label("total_invoiced"),
                func.coalesce(func.sum(paid_amount), 0).label("total_collected"),
                func.coalesce(func.sum(balance), 0).label("total_outstanding"),
                func.count(invoice_table.c.id).label("invoice_count"),
                func.coalesce(func.sum(paid_invoice), 0).label("paid_invoice_count"),
                func.coalesce(func.sum(overdue_invoice), 0).label("overdue_invoice_count"),
            )
            .select_from(from_clause)
            .where(*filters)
            .group_by(invoice_table.c.currency)
            .order_by(invoice_table.c.currency)
        )
        summaries = []
        for row in summary_result.mappings():
            total_invoiced = Decimal(str(row["total_invoiced"]))
            total_collected = Decimal(str(row["total_collected"]))
            summaries.append(
                BillingCurrencySummary(
                    currency=row["currency"],
                    total_invoiced=total_invoiced,
                    total_collected=total_collected,
                    total_outstanding=Decimal(str(row["total_outstanding"])),
                    collection_rate=(
                        (total_collected / total_invoiced * Decimal("100"))
                        if total_invoiced > 0
                        else Decimal("0")
                    ),
                    invoice_count=row["invoice_count"],
                    paid_invoice_count=row["paid_invoice_count"],
                    overdue_invoice_count=row["overdue_invoice_count"],
                )
            )

        defaulter_filters = [
            *filters,
            invoice_table.c.due_date < date.today(),
            raw_balance > 0,
        ]
        defaulters_count_result = await session.execute(
            select(func.count(invoice_table.c.id))
            .select_from(from_clause)
            .where(*defaulter_filters)
        )
        defaulters_result = await session.execute(
            select(
                invoice_table.c.id.label("invoice_id"),
                invoice_table.c.invoice_number,
                student_table.c.id.label("student_id"),
                student_table.c.first_name,
                student_table.c.last_name,
                student_table.c.admission_number,
                class_table.c.name.label("class_name"),
                term_table.c.name.label("term_name"),
                invoice_table.c.due_date,
                balance.label("balance"),
                invoice_table.c.currency,
            )
            .select_from(from_clause)
            .where(*defaulter_filters)
            .order_by(invoice_table.c.due_date, student_table.c.last_name, student_table.c.first_name)
            .limit(defaulters_limit)
        )
        today = date.today()
        defaulters = [
            BillingDefaulterResponse(
                invoice_id=row["invoice_id"],
                invoice_number=row["invoice_number"],
                student_id=row["student_id"],
                student_name=f'{row["first_name"]} {row["last_name"]}',
                admission_number=row["admission_number"],
                class_name=row["class_name"],
                term_name=row["term_name"],
                due_date=row["due_date"],
                days_overdue=(today - row["due_date"]).days,
                balance=Decimal(str(row["balance"])),
                currency=row["currency"],
            )
            for row in defaulters_result.mappings()
        ]
        return BillingDashboardResponse(
            summaries=summaries,
            defaulters=defaulters,
            defaulters_total=defaulters_count_result.scalar_one(),
        )

    @app.post(
        "/api/v1/billing/invoices/{invoice_id}/payments",
        response_model=InvoiceResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Billing"],
    )
    async def record_invoice_payment(
        invoice_id: str,
        payload: PaymentCreate,
        user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> InvoiceResponse:
        invoice_table, payment_table = tenant_billing_tables(session)[2:]
        invoice_result = await session.execute(
            select(invoice_table).where(
                invoice_table.c.id == invoice_id,
                invoice_table.c.voided_at.is_(None),
            ).with_for_update()
        )
        invoice = invoice_result.mappings().one_or_none()
        if invoice is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
        paid_result = await session.execute(
            select(func.coalesce(func.sum(payment_table.c.amount), 0)).where(
                payment_table.c.invoice_id == invoice_id,
                payment_table.c.voided_at.is_(None),
            )
        )
        balance = invoice["amount"] - Decimal(str(paid_result.scalar_one()))
        if payload.amount > balance:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Payment cannot exceed the invoice balance",
            )
        try:
            await session.execute(
                insert(payment_table).values(
                    id=uuid.uuid4().hex,
                    invoice_id=invoice_id,
                    amount=payload.amount,
                    method=payload.method,
                    reference=payload.reference,
                    paid_at=payload.paid_at,
                    recorded_by_user_id=user.id,
                    voided_at=None,
                    void_reason=None,
                    created_at=datetime.now(UTC),
                )
            )
        except IntegrityError as error:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A payment with this reference already exists",
            ) from error
        new_status = "paid" if payload.amount == balance else (
            "overdue" if invoice["due_date"] < datetime.now(UTC).date() else "issued"
        )
        await session.execute(
            update(invoice_table).where(invoice_table.c.id == invoice_id).values(status=new_status)
        )
        await session.commit()
        result = await session.execute(
            invoice_list_statement(session, invoice_table).where(invoice_table.c.id == invoice_id)
        )
        return (await invoice_responses(session, result.mappings().all()))[0]

    @app.post(
        "/api/v1/billing/payments/{payment_id}/void",
        response_model=InvoiceResponse,
        tags=["Billing"],
    )
    async def void_invoice_payment(
        payment_id: str,
        payload: PaymentVoidRequest,
        _user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> InvoiceResponse:
        invoice_table, payment_table = tenant_billing_tables(session)[2:]
        result = await session.execute(
            select(payment_table).where(
                payment_table.c.id == payment_id,
                payment_table.c.voided_at.is_(None),
            ).with_for_update()
        )
        payment = result.mappings().one_or_none()
        if payment is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active payment not found")
        invoice_result = await session.execute(
            select(invoice_table).where(invoice_table.c.id == payment["invoice_id"])
        )
        invoice = invoice_result.mappings().one()
        await session.execute(
            update(payment_table)
            .where(payment_table.c.id == payment_id)
            .values(voided_at=datetime.now(UTC), void_reason=payload.reason)
        )
        remaining_result = await session.execute(
            select(func.coalesce(func.sum(payment_table.c.amount), 0)).where(
                payment_table.c.invoice_id == payment["invoice_id"],
                payment_table.c.voided_at.is_(None),
                payment_table.c.id != payment_id,
            )
        )
        remaining_paid = Decimal(str(remaining_result.scalar_one()))
        status_value = "paid" if remaining_paid >= invoice["amount"] else (
            "overdue" if invoice["due_date"] < datetime.now(UTC).date() else "issued"
        )
        await session.execute(
            update(invoice_table)
            .where(invoice_table.c.id == payment["invoice_id"])
            .values(status=status_value)
        )
        await session.commit()
        invoice_result = await session.execute(
            invoice_list_statement(session, invoice_table).where(invoice_table.c.id == payment["invoice_id"])
        )
        return (await invoice_responses(session, invoice_result.mappings().all()))[0]

    @app.post(
        "/api/v1/parents/invoices/{invoice_id}/checkout",
        response_model=BillingCheckoutResponse,
        tags=["Parents"],
    )
    async def create_parent_invoice_checkout(
        invoice_id: str,
        tenant: Annotated[Tenant, Depends(get_request_tenant)],
        user: Annotated[TenantUser, Depends(require_guardian)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> BillingCheckoutResponse:
        settings_table, settings = await billing_settings_for(session)
        del settings_table
        provider = settings["provider"] if settings is not None else "manual"
        if provider == "manual":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Online checkout is not enabled for this school",
            )
        credentials = decrypt_billing_credentials(
            settings["credentials_encrypted"] if settings is not None else None
        ).get(provider, {})
        secret_key = credentials.get("secret_key")
        webhook_secret = credentials.get("webhook_secret")
        if not secret_key or (provider == "stripe" and not webhook_secret):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The school's online payment provider is not fully configured",
            )

        student_table, _, _class_table, enrollment_table = tenant_academic_tables(session)
        billing_invoice_table = tenant_billing_tables(session)[2]
        invoice_result = await session.execute(
            select(
                billing_invoice_table,
                student_table.c.guardian_email,
            )
            .select_from(
                billing_invoice_table.join(
                    enrollment_table,
                    billing_invoice_table.c.enrollment_id == enrollment_table.c.id,
                ).join(student_table, enrollment_table.c.student_id == student_table.c.id)
            )
            .where(
                billing_invoice_table.c.id == invoice_id,
                billing_invoice_table.c.voided_at.is_(None),
            )
        )
        invoice = invoice_result.mappings().one_or_none()
        if invoice is None or (
            invoice["guardian_email"] is None
            or invoice["guardian_email"].lower() != user.email.lower()
        ):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

        payment_table = tenant_billing_tables(session)[3]
        paid_result = await session.execute(
            select(func.coalesce(func.sum(payment_table.c.amount), 0)).where(
                payment_table.c.invoice_id == invoice_id,
                payment_table.c.voided_at.is_(None),
            )
        )
        balance = invoice["amount"] - Decimal(str(paid_result.scalar_one()))
        if balance <= 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This invoice has no remaining balance",
            )
        currency = invoice["currency"]
        minor_amount = amount_to_minor_units(balance, currency)
        reference = f"ns-{invoice_id}-{uuid.uuid4().hex[:16]}"
        callback_url = tenant_app_url(tenant, "/parent?checkout=complete")
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                if provider == "paystack":
                    response = await client.post(
                        "https://api.paystack.co/transaction/initialize",
                        headers={"Authorization": f"Bearer {secret_key}"},
                        json={
                            "email": user.email,
                            "amount": minor_amount,
                            "currency": currency,
                            "reference": reference,
                            "callback_url": callback_url,
                            "metadata": {"invoice_id": invoice_id},
                        },
                    )
                else:
                    response = await client.post(
                        "https://api.stripe.com/v1/checkout/sessions",
                        auth=(secret_key, ""),
                        data={
                            "mode": "payment",
                            "line_items[0][price_data][currency]": currency.lower(),
                            "line_items[0][price_data][product_data][name]": (
                                f"School invoice {invoice['invoice_number']}"
                            ),
                            "line_items[0][price_data][unit_amount]": str(minor_amount),
                            "line_items[0][quantity]": "1",
                            "client_reference_id": reference,
                            "metadata[invoice_id]": invoice_id,
                            "success_url": (
                                f"{callback_url}&session_id={{CHECKOUT_SESSION_ID}}"
                            ),
                            "cancel_url": tenant_app_url(
                                tenant, "/parent?checkout=cancelled"
                            ),
                        },
                    )
                response.raise_for_status()
                provider_result = response.json()
        except (httpx.HTTPError, ValueError) as error:
            logger.warning("Tenant invoice checkout initialization failed for provider %s", provider)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="The payment provider could not start checkout; try again later",
            ) from error

        if provider == "paystack":
            data = provider_result.get("data")
            checkout_url = data.get("authorization_url") if isinstance(data, dict) else None
            returned_reference = data.get("reference") if isinstance(data, dict) else None
        else:
            checkout_url = provider_result.get("url")
            returned_reference = provider_result.get("id")
        if (
            provider_result.get("status", True) is False
            or not isinstance(checkout_url, str)
            or not checkout_url.startswith("https://")
            or not isinstance(returned_reference, str)
        ):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="The payment provider returned an invalid checkout response",
            )
        return BillingCheckoutResponse(
            provider=provider,
            checkout_url=checkout_url,
            reference=returned_reference,
        )

    async def apply_verified_gateway_payment(
        provider: Literal["paystack", "stripe"],
        event: Mapping[str, Any],
        session: AsyncSession,
    ) -> bool:
        if provider == "paystack":
            if event.get("event") != "charge.success":
                return False
            transaction = event.get("data")
            if not isinstance(transaction, dict) or transaction.get("status") != "success":
                return False
            metadata = transaction.get("metadata")
            reference = transaction.get("reference")
            amount_minor = transaction.get("amount")
            currency = transaction.get("currency")
            invoice_id = metadata.get("invoice_id") if isinstance(metadata, dict) else None
        else:
            if event.get("type") not in {
                "checkout.session.completed",
                "checkout.session.async_payment_succeeded",
            }:
                return False
            data = event.get("data")
            checkout_session = data.get("object") if isinstance(data, dict) else None
            if not isinstance(checkout_session, dict) or checkout_session.get("payment_status") != "paid":
                return False
            metadata = checkout_session.get("metadata")
            reference = checkout_session.get("id")
            amount_minor = checkout_session.get("amount_total")
            currency = checkout_session.get("currency")
            invoice_id = metadata.get("invoice_id") if isinstance(metadata, dict) else None

        if (
            not isinstance(invoice_id, str)
            or not isinstance(reference, str)
            or not isinstance(amount_minor, int)
            or isinstance(amount_minor, bool)
            or amount_minor <= 0
            or not isinstance(currency, str)
            or len(reference) > 128
        ):
            return False
        invoice_table, payment_table = tenant_billing_tables(session)[2:]
        existing_payment = await session.execute(
            select(payment_table.c.id).where(payment_table.c.reference == reference)
        )
        if existing_payment.scalar_one_or_none() is not None:
            return True
        invoice_result = await session.execute(
            select(invoice_table).where(
                invoice_table.c.id == invoice_id,
                invoice_table.c.voided_at.is_(None),
            ).with_for_update()
        )
        invoice = invoice_result.mappings().one_or_none()
        if invoice is None or invoice["currency"] != currency.upper():
            return False
        paid_result = await session.execute(
            select(func.coalesce(func.sum(payment_table.c.amount), 0)).where(
                payment_table.c.invoice_id == invoice_id,
                payment_table.c.voided_at.is_(None),
            )
        )
        paid_amount = Decimal(str(paid_result.scalar_one()))
        balance = invoice["amount"] - paid_amount
        if balance <= 0 or amount_from_minor_units(amount_minor, currency.upper()) != balance:
            logger.warning("Ignoring unmatched %s payment for invoice %s", provider, invoice_id)
            return False

        await session.execute(
            insert(payment_table).values(
                id=uuid.uuid4().hex,
                invoice_id=invoice_id,
                amount=balance,
                method=provider,
                reference=reference,
                paid_at=datetime.now(UTC),
                recorded_by_user_id=None,
                voided_at=None,
                void_reason=None,
                created_at=datetime.now(UTC),
            )
        )
        await session.execute(
            update(invoice_table)
            .where(invoice_table.c.id == invoice_id)
            .values(status="paid")
        )
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            duplicate_result = await session.execute(
                select(payment_table.c.id).where(payment_table.c.reference == reference)
            )
            return duplicate_result.scalar_one_or_none() is not None
        return True

    @app.post(
        "/api/v1/billing/webhooks/paystack",
        tags=["Billing"],
    )
    async def paystack_billing_webhook(
        request: Request,
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> dict[str, bool]:
        raw_body = await request.body()
        try:
            event = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise HTTPException(status_code=400, detail="Invalid webhook payload") from error
        if not isinstance(event, dict):
            raise HTTPException(status_code=400, detail="Invalid webhook payload")
        _table, settings = await billing_settings_for(session)
        if settings is None or settings["provider"] != "paystack":
            raise HTTPException(status_code=404, detail="Paystack is not configured for this school")
        credentials = decrypt_billing_credentials(settings["credentials_encrypted"])
        secret_key = credentials.get("paystack", {}).get("secret_key")
        signature = request.headers.get("x-paystack-signature")
        if not secret_key or not signature:
            raise HTTPException(status_code=401, detail="Webhook signature is missing")
        expected = hmac.new(
            secret_key.encode("utf-8"),
            raw_body,
            hashlib.sha512,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
        applied = await apply_verified_gateway_payment("paystack", event, session)
        return {"received": True, "applied": applied}

    @app.post(
        "/api/v1/billing/webhooks/stripe",
        tags=["Billing"],
    )
    async def stripe_billing_webhook(
        request: Request,
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> dict[str, bool]:
        raw_body = await request.body()
        try:
            event = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise HTTPException(status_code=400, detail="Invalid webhook payload") from error
        if not isinstance(event, dict):
            raise HTTPException(status_code=400, detail="Invalid webhook payload")
        _table, settings = await billing_settings_for(session)
        if settings is None or settings["provider"] != "stripe":
            raise HTTPException(status_code=404, detail="Stripe is not configured for this school")
        credentials = decrypt_billing_credentials(settings["credentials_encrypted"])
        webhook_secret = credentials.get("stripe", {}).get("webhook_secret")
        signature_header = request.headers.get("stripe-signature", "")
        try:
            signature_parts = [
                (key, value)
                for part in signature_header.split(",")
                if "=" in part
                for key, value in [part.strip().split("=", 1)]
            ]
            timestamps = [value for key, value in signature_parts if key == "t"]
            if not timestamps:
                raise ValueError("Missing webhook timestamp")
            timestamp = int(timestamps[0])
        except ValueError as error:
            raise HTTPException(status_code=401, detail="Invalid webhook signature") from error
        if (
            not webhook_secret
            or abs(int(time.time()) - timestamp) > 300
            or not any(
                hmac.compare_digest(
                    hmac.new(
                        webhook_secret.encode("utf-8"),
                        str(timestamp).encode("ascii") + b"." + raw_body,
                        hashlib.sha256,
                    ).hexdigest(),
                    supplied_signature,
                )
                for key, supplied_signature in signature_parts
                if key == "v1"
                if supplied_signature
            )
        ):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
        applied = await apply_verified_gateway_payment("stripe", event, session)
        return {"received": True, "applied": applied}

    async def announcement_responses(
        session: AsyncSession,
        rows: Sequence[Mapping[str, Any]],
    ) -> list[AnnouncementResponse]:
        if not rows:
            return []
        delivery_table = tenant_announcement_tables(session)[1]
        announcement_ids = [str(row["id"]) for row in rows]
        counts_result = await session.execute(
            select(
                delivery_table.c.announcement_id,
                delivery_table.c.status,
                func.count().label("count"),
            )
            .where(delivery_table.c.announcement_id.in_(announcement_ids))
            .group_by(delivery_table.c.announcement_id, delivery_table.c.status)
        )
        counts: dict[str, dict[str, int]] = {}
        for count_row in counts_result.mappings():
            counts.setdefault(count_row["announcement_id"], {})[count_row["status"]] = count_row["count"]
        return [
            AnnouncementResponse(
                id=row["id"],
                title=row["title"],
                body=row["body"],
                status=row["status"],
                created_at=row["created_at"],
                published_at=row["published_at"],
                recipient_count=sum(counts.get(row["id"], {}).values()),
                delivered_count=counts.get(row["id"], {}).get("sent", 0),
                failed_count=counts.get(row["id"], {}).get("failed", 0),
                pending_count=counts.get(row["id"], {}).get("pending", 0),
            )
            for row in rows
        ]

    async def deliver_announcement_emails(
        session: AsyncSession,
        announcement_id: str,
        title: str,
        body: str,
        statuses: Sequence[str],
    ) -> AnnouncementResponse:
        announcement_table, delivery_table = tenant_announcement_tables(session)
        delivery_result = await session.execute(
            select(
                delivery_table.c.id,
                delivery_table.c.recipient_email,
            )
            .where(
                delivery_table.c.announcement_id == announcement_id,
                delivery_table.c.status.in_(statuses),
            )
            .order_by(delivery_table.c.recipient_email)
        )
        deliveries = delivery_result.mappings().all()
        subject = f"School announcement: {' '.join(title.split())}"
        for delivery in deliveries:
            try:
                await deliver_action_email(
                    delivery["recipient_email"],
                    subject,
                    f"{title}\n\n{body}",
                )
            except Exception as error:
                logger.exception(
                    "Announcement email delivery failed for announcement %s",
                    announcement_id,
                )
                await session.execute(
                    update(delivery_table)
                    .where(delivery_table.c.id == delivery["id"])
                    .values(
                        status="failed",
                        last_error=str(error)[:500],
                        updated_at=func.now(),
                    )
                )
            else:
                await session.execute(
                    update(delivery_table)
                    .where(delivery_table.c.id == delivery["id"])
                    .values(
                        status="sent",
                        sent_at=datetime.now(UTC),
                        last_error=None,
                        updated_at=func.now(),
                    )
                )
        await session.commit()
        announcement_result = await session.execute(
            select(
                announcement_table.c.id,
                announcement_table.c.title,
                announcement_table.c.body,
                announcement_table.c.status,
                announcement_table.c.created_at,
                announcement_table.c.published_at,
            ).where(announcement_table.c.id == announcement_id)
        )
        announcement_row = announcement_result.mappings().one()
        return (await announcement_responses(session, [announcement_row]))[0]

    @app.get(
        "/api/v1/announcements",
        response_model=AnnouncementListResponse,
        tags=["Announcements"],
    )
    async def list_announcements(
        _user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
        limit: int = 50,
    ) -> AnnouncementListResponse:
        if not 1 <= limit <= 100:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Announcement limit must be between 1 and 100",
            )
        announcement_table = tenant_announcement_tables(session)[0]
        total_result = await session.execute(select(func.count()).select_from(announcement_table))
        rows_result = await session.execute(
            select(
                announcement_table.c.id,
                announcement_table.c.title,
                announcement_table.c.body,
                announcement_table.c.status,
                announcement_table.c.created_at,
                announcement_table.c.published_at,
            )
            .order_by(announcement_table.c.created_at.desc())
            .limit(limit)
        )
        rows = rows_result.mappings().all()
        return AnnouncementListResponse(
            items=await announcement_responses(session, rows),
            total=total_result.scalar_one(),
        )

    @app.post(
        "/api/v1/announcements",
        response_model=AnnouncementResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Announcements"],
    )
    async def create_announcement(
        request: AnnouncementCreate,
        user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> AnnouncementResponse:
        announcement_table = tenant_announcement_tables(session)[0]
        announcement_id = uuid.uuid4().hex
        created_at = datetime.now(UTC)
        await session.execute(
            insert(announcement_table).values(
                id=announcement_id,
                title=request.title,
                body=request.body,
                status="draft",
                created_by_user_id=user.id,
                created_at=created_at,
                published_at=None,
            )
        )
        await session.commit()
        return AnnouncementResponse(
            id=announcement_id,
            title=request.title,
            body=request.body,
            status="draft",
            created_at=created_at,
            published_at=None,
            recipient_count=0,
            delivered_count=0,
            failed_count=0,
            pending_count=0,
        )

    @app.post(
        "/api/v1/announcements/{announcement_id}/publish",
        response_model=AnnouncementResponse,
        tags=["Announcements"],
    )
    async def publish_announcement(
        announcement_id: str,
        _user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> AnnouncementResponse:
        announcement_table, delivery_table = tenant_announcement_tables(session)
        announcement_result = await session.execute(
            select(
                announcement_table.c.id,
                announcement_table.c.title,
                announcement_table.c.body,
                announcement_table.c.status,
            ).where(announcement_table.c.id == announcement_id)
        )
        announcement = announcement_result.mappings().one_or_none()
        if announcement is None:
            raise HTTPException(status_code=404, detail="Announcement not found")
        if announcement["status"] != "draft":
            raise HTTPException(status_code=409, detail="Announcement has already been published")

        student_table = tenant_students_table(session)
        recipient_result = await session.execute(
            select(func.lower(student_table.c.guardian_email))
            .where(
                student_table.c.is_active.is_(True),
                student_table.c.guardian_email.is_not(None),
            )
            .distinct()
        )
        recipients = sorted(
            {
                email.strip().lower()
                for email in recipient_result.scalars()
                if email and email.strip()
            }
        )
        if recipients:
            require_email_configuration()

        published_at = datetime.now(UTC)
        publish_result = await session.execute(
            update(announcement_table)
            .where(
                announcement_table.c.id == announcement_id,
                announcement_table.c.status == "draft",
            )
            .values(status="published", published_at=published_at)
        )
        if publish_result.rowcount != 1:
            await session.rollback()
            raise HTTPException(status_code=409, detail="Announcement has already been published")
        if recipients:
            await session.execute(
                insert(delivery_table),
                [
                    {
                        "id": uuid.uuid4().hex,
                        "announcement_id": announcement_id,
                        "recipient_email": recipient,
                        "status": "pending",
                        "sent_at": None,
                        "last_error": None,
                        "updated_at": published_at,
                    }
                    for recipient in recipients
                ],
            )
        await session.commit()
        return await deliver_announcement_emails(
            session,
            announcement_id,
            announcement["title"],
            announcement["body"],
            ("pending",),
        )

    @app.post(
        "/api/v1/announcements/{announcement_id}/deliveries/retry",
        response_model=AnnouncementResponse,
        tags=["Announcements"],
    )
    async def retry_announcement_emails(
        announcement_id: str,
        _user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> AnnouncementResponse:
        announcement_table = tenant_announcement_tables(session)[0]
        announcement_result = await session.execute(
            select(
                announcement_table.c.id,
                announcement_table.c.title,
                announcement_table.c.body,
                announcement_table.c.status,
            ).where(announcement_table.c.id == announcement_id)
        )
        announcement = announcement_result.mappings().one_or_none()
        if announcement is None:
            raise HTTPException(status_code=404, detail="Announcement not found")
        if announcement["status"] != "published":
            raise HTTPException(status_code=409, detail="Only published announcements can be retried")
        require_email_configuration()
        return await deliver_announcement_emails(
            session,
            announcement_id,
            announcement["title"],
            announcement["body"],
            ("pending", "failed"),
        )

    def message_thread_query(
        session: AsyncSession,
        user: TenantUser,
    ) -> tuple[Any, Table, Table, Table]:
        if user.role not in {"teacher", "guardian"}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Teacher or guardian access required",
            )
        thread_table, message_table, flag_table = tenant_messaging_tables(session)
        student_table, _year_table, class_table, enrollment_table = tenant_academic_tables(session)
        assignment_table = tenant_teacher_assignment_table(session)
        user_table = tenant_users_table(session)
        teacher_table = user_table.alias("message_teacher")
        guardian_table = user_table.alias("message_guardian")
        message_counts = (
            select(
                message_table.c.thread_id,
                func.count(message_table.c.id).label("message_count"),
            )
            .group_by(message_table.c.thread_id)
            .subquery()
        )
        latest_message = (
            select(message_table.c.body)
            .where(message_table.c.thread_id == thread_table.c.id)
            .order_by(message_table.c.created_at.desc(), message_table.c.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        from_clause = (
            thread_table.join(
                student_table,
                thread_table.c.student_id == student_table.c.id,
            )
            .join(
                enrollment_table,
                and_(
                    enrollment_table.c.student_id == student_table.c.id,
                    enrollment_table.c.status == "active",
                ),
            )
            .join(
                class_table,
                and_(
                    class_table.c.id == enrollment_table.c.class_section_id,
                    class_table.c.is_active.is_(True),
                ),
            )
            .join(
                assignment_table,
                and_(
                    assignment_table.c.class_section_id == class_table.c.id,
                    assignment_table.c.teacher_user_id == thread_table.c.teacher_user_id,
                ),
            )
            .join(teacher_table, teacher_table.c.id == thread_table.c.teacher_user_id)
            .join(guardian_table, guardian_table.c.id == thread_table.c.guardian_user_id)
            .outerjoin(message_counts, message_counts.c.thread_id == thread_table.c.id)
        )
        filters = [student_table.c.is_active.is_(True)]
        if user.role == "teacher":
            filters.extend(
                [
                    thread_table.c.teacher_user_id == user.id,
                    teacher_table.c.is_active.is_(True),
                ]
            )
        else:
            filters.extend(
                [
                    thread_table.c.guardian_user_id == user.id,
                    func.lower(student_table.c.guardian_email) == user.email.lower(),
                    guardian_table.c.is_active.is_(True),
                ]
            )
        query = (
            select(
                thread_table.c.id,
                thread_table.c.student_id,
                student_table.c.first_name.label("student_first_name"),
                student_table.c.last_name.label("student_last_name"),
                thread_table.c.teacher_user_id.label("teacher_id"),
                teacher_table.c.email.label("teacher_email"),
                thread_table.c.guardian_user_id.label("guardian_id"),
                guardian_table.c.email.label("guardian_email"),
                func.coalesce(message_counts.c.message_count, 0).label("message_count"),
                latest_message.label("latest_message"),
                thread_table.c.updated_at,
            )
            .select_from(from_clause)
            .where(*filters)
            .distinct()
        )
        return query, thread_table, message_table, flag_table

    async def visible_message_thread(
        session: AsyncSession,
        user: TenantUser,
        thread_id: str,
    ) -> Mapping[str, Any]:
        query, _thread_table, _message_table, _flag_table = message_thread_query(session, user)
        result = await session.execute(query.where(query.selected_columns.id == thread_id))
        thread = result.mappings().first()
        if thread is None:
            raise HTTPException(status_code=404, detail="Message thread not found")
        return thread

    def message_thread_summary(row: Mapping[str, Any]) -> MessageThreadSummary:
        return MessageThreadSummary(
            id=row["id"],
            student_id=row["student_id"],
            student_name=f"{row['student_first_name']} {row['student_last_name']}",
            teacher_id=row["teacher_id"],
            teacher_email=row["teacher_email"],
            guardian_id=row["guardian_id"],
            guardian_email=row["guardian_email"],
            message_count=row["message_count"],
            latest_message=row["latest_message"],
            updated_at=row["updated_at"],
        )

    async def message_thread_detail(
        session: AsyncSession,
        user: TenantUser,
        thread_id: str,
    ) -> MessageThreadDetail:
        thread = await visible_message_thread(session, user, thread_id)
        _thread_table, message_table, flag_table = tenant_messaging_tables(session)
        user_table = tenant_users_table(session)
        message_result = await session.execute(
            select(
                message_table.c.id,
                message_table.c.sender_user_id,
                user_table.c.email.label("sender_email"),
                message_table.c.body,
                message_table.c.created_at,
                flag_table.c.status.label("flag_status"),
            )
            .select_from(
                message_table.join(
                    user_table,
                    message_table.c.sender_user_id == user_table.c.id,
                ).outerjoin(
                    flag_table,
                    flag_table.c.message_id == message_table.c.id,
                )
            )
            .where(message_table.c.thread_id == thread_id)
            .order_by(message_table.c.created_at, message_table.c.id)
        )
        messages = [
            ThreadMessageResponse(
                **dict(row),
                can_flag=row["sender_user_id"] != user.id and row["flag_status"] is None,
            )
            for row in message_result.mappings()
        ]
        return MessageThreadDetail(**message_thread_summary(thread).model_dump(), messages=messages)

    def moderation_flag_query(session: AsyncSession) -> Any:
        thread_table, message_table, flag_table = tenant_messaging_tables(session)
        student_table = tenant_students_table(session)
        user_table = tenant_users_table(session)
        teacher_table = user_table.alias("flag_teacher")
        guardian_table = user_table.alias("flag_guardian")
        sender_table = user_table.alias("flag_sender")
        reporter_table = user_table.alias("flag_reporter")
        return (
            select(
                flag_table.c.id,
                flag_table.c.message_id,
                thread_table.c.id.label("thread_id"),
                (
                    student_table.c.first_name + " " + student_table.c.last_name
                ).label("student_name"),
                teacher_table.c.email.label("teacher_email"),
                guardian_table.c.email.label("guardian_email"),
                sender_table.c.email.label("sender_email"),
                reporter_table.c.email.label("reported_by_email"),
                message_table.c.body,
                flag_table.c.reason,
                flag_table.c.status,
                flag_table.c.created_at,
                flag_table.c.reviewed_at,
            )
            .select_from(
                flag_table.join(
                    message_table,
                    flag_table.c.message_id == message_table.c.id,
                )
                .join(
                    thread_table,
                    message_table.c.thread_id == thread_table.c.id,
                )
                .join(student_table, thread_table.c.student_id == student_table.c.id)
                .join(teacher_table, thread_table.c.teacher_user_id == teacher_table.c.id)
                .join(guardian_table, thread_table.c.guardian_user_id == guardian_table.c.id)
                .join(sender_table, message_table.c.sender_user_id == sender_table.c.id)
                .join(reporter_table, flag_table.c.reported_by_user_id == reporter_table.c.id)
            )
        )

    async def get_moderation_flag(
        session: AsyncSession,
        flag_id: str,
    ) -> MessageFlagResponse:
        query = moderation_flag_query(session)
        result = await session.execute(query.where(query.selected_columns.id == flag_id))
        row = result.mappings().one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Message flag not found")
        return MessageFlagResponse(**dict(row))

    @app.get(
        "/api/v1/parents/children/{student_id}/teachers",
        response_model=list[ParentTeacherContact],
        tags=["Messages"],
    )
    async def list_parent_teacher_contacts(
        student_id: str,
        user: Annotated[TenantUser, Depends(require_guardian)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> list[ParentTeacherContact]:
        student_table, _year_table, class_table, enrollment_table = tenant_academic_tables(session)
        assignment_table = tenant_teacher_assignment_table(session)
        user_table = tenant_users_table(session)
        result = await session.execute(
            select(
                user_table.c.id.label("teacher_id"),
                user_table.c.email,
                class_table.c.name.label("class_name"),
            )
            .select_from(
                student_table.join(
                    enrollment_table,
                    and_(
                        enrollment_table.c.student_id == student_table.c.id,
                        enrollment_table.c.status == "active",
                    ),
                )
                .join(
                    class_table,
                    and_(
                        class_table.c.id == enrollment_table.c.class_section_id,
                        class_table.c.is_active.is_(True),
                    ),
                )
                .join(
                    assignment_table,
                    assignment_table.c.class_section_id == class_table.c.id,
                )
                .join(user_table, user_table.c.id == assignment_table.c.teacher_user_id)
            )
            .where(
                student_table.c.id == student_id,
                student_table.c.is_active.is_(True),
                func.lower(student_table.c.guardian_email) == user.email.lower(),
                user_table.c.role == "teacher",
                user_table.c.is_active.is_(True),
            )
            .distinct()
            .order_by(user_table.c.email, class_table.c.name)
        )
        return [
            ParentTeacherContact(**dict(row))
            for row in result.mappings()
        ]

    @app.get(
        "/api/v1/messages/threads",
        response_model=MessageThreadListResponse,
        tags=["Messages"],
    )
    async def list_message_threads(
        user: Annotated[TenantUser, Depends(require_school_user)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
        limit: int = 100,
    ) -> MessageThreadListResponse:
        if not 1 <= limit <= 100:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Thread limit must be between 1 and 100",
            )
        query, _thread_table, _message_table, _flag_table = message_thread_query(session, user)
        total_result = await session.execute(
            select(func.count()).select_from(query.order_by(None).subquery())
        )
        result = await session.execute(query.order_by(query.selected_columns.updated_at.desc()).limit(limit))
        rows = result.mappings().all()
        return MessageThreadListResponse(
            items=[message_thread_summary(row) for row in rows],
            total=total_result.scalar_one(),
        )

    @app.post(
        "/api/v1/messages/threads",
        response_model=MessageThreadDetail,
        status_code=status.HTTP_201_CREATED,
        tags=["Messages"],
    )
    async def create_message_thread(
        request: MessageThreadCreate,
        user: Annotated[TenantUser, Depends(require_guardian)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> MessageThreadDetail:
        student_table, _year_table, class_table, enrollment_table = tenant_academic_tables(session)
        assignment_table = tenant_teacher_assignment_table(session)
        user_table = tenant_users_table(session)
        eligible_result = await session.execute(
            select(user_table.c.id)
            .select_from(
                student_table.join(
                    enrollment_table,
                    and_(
                        enrollment_table.c.student_id == student_table.c.id,
                        enrollment_table.c.status == "active",
                    ),
                )
                .join(
                    class_table,
                    and_(
                        class_table.c.id == enrollment_table.c.class_section_id,
                        class_table.c.is_active.is_(True),
                    ),
                )
                .join(
                    assignment_table,
                    and_(
                        assignment_table.c.class_section_id == class_table.c.id,
                        assignment_table.c.teacher_user_id == request.teacher_id,
                    ),
                )
                .join(user_table, user_table.c.id == assignment_table.c.teacher_user_id)
            )
            .where(
                student_table.c.id == request.student_id,
                student_table.c.is_active.is_(True),
                func.lower(student_table.c.guardian_email) == user.email.lower(),
                user_table.c.role == "teacher",
                user_table.c.is_active.is_(True),
            )
            .limit(1)
        )
        if eligible_result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Student or assigned teacher not found")

        thread_table = tenant_messaging_tables(session)[0]
        existing_result = await session.execute(
            select(thread_table.c.id).where(
                thread_table.c.student_id == request.student_id,
                thread_table.c.teacher_user_id == request.teacher_id,
                thread_table.c.guardian_user_id == user.id,
            )
        )
        thread_id = existing_result.scalar_one_or_none()
        if thread_id is None:
            thread_id = uuid.uuid4().hex
            try:
                await session.execute(
                    insert(thread_table).values(
                        id=thread_id,
                        student_id=request.student_id,
                        teacher_user_id=request.teacher_id,
                        guardian_user_id=user.id,
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    )
                )
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing_result = await session.execute(
                    select(thread_table.c.id).where(
                        thread_table.c.student_id == request.student_id,
                        thread_table.c.teacher_user_id == request.teacher_id,
                        thread_table.c.guardian_user_id == user.id,
                    )
                )
                thread_id = existing_result.scalar_one_or_none()
                if thread_id is None:
                    raise HTTPException(status_code=409, detail="Message thread could not be created")
        return await message_thread_detail(session, user, thread_id)

    @app.get(
        "/api/v1/messages/threads/{thread_id}",
        response_model=MessageThreadDetail,
        tags=["Messages"],
    )
    async def get_message_thread(
        thread_id: str,
        user: Annotated[TenantUser, Depends(require_school_user)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> MessageThreadDetail:
        return await message_thread_detail(session, user, thread_id)

    @app.post(
        "/api/v1/messages/threads/{thread_id}/messages",
        response_model=ThreadMessageResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Messages"],
    )
    async def create_thread_message(
        thread_id: str,
        request: ThreadMessageCreate,
        user: Annotated[TenantUser, Depends(require_school_user)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> ThreadMessageResponse:
        await visible_message_thread(session, user, thread_id)
        thread_table, message_table, _flag_table = tenant_messaging_tables(session)
        message_id = uuid.uuid4().hex
        created_at = datetime.now(UTC)
        await session.execute(
            insert(message_table).values(
                id=message_id,
                thread_id=thread_id,
                sender_user_id=user.id,
                body=request.body,
                created_at=created_at,
            )
        )
        await session.execute(
            update(thread_table)
            .where(thread_table.c.id == thread_id)
            .values(updated_at=created_at)
        )
        await session.commit()
        return ThreadMessageResponse(
            id=message_id,
            sender_user_id=user.id,
            sender_email=user.email,
            body=request.body,
            created_at=created_at,
            flag_status=None,
            can_flag=False,
        )

    @app.post(
        "/api/v1/messages/{message_id}/flags",
        response_model=MessageFlagResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Messages"],
    )
    async def flag_thread_message(
        message_id: str,
        request: MessageFlagCreate,
        user: Annotated[TenantUser, Depends(require_school_user)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> MessageFlagResponse:
        _thread_table, message_table, flag_table = tenant_messaging_tables(session)
        message_result = await session.execute(
            select(message_table.c.thread_id, message_table.c.sender_user_id).where(
                message_table.c.id == message_id
            )
        )
        message = message_result.mappings().one_or_none()
        if message is None:
            raise HTTPException(status_code=404, detail="Message not found")
        if message["sender_user_id"] == user.id:
            raise HTTPException(status_code=409, detail="You cannot flag your own message")
        await visible_message_thread(session, user, message["thread_id"])
        flag_id = uuid.uuid4().hex
        created_at = datetime.now(UTC)
        try:
            await session.execute(
                insert(flag_table).values(
                    id=flag_id,
                    message_id=message_id,
                    reported_by_user_id=user.id,
                    reason=request.reason,
                    status="open",
                    created_at=created_at,
                    reviewed_by_user_id=None,
                    reviewed_at=None,
                )
            )
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            raise HTTPException(status_code=409, detail="This message has already been flagged") from error
        moderation_flag = await get_moderation_flag(session, flag_id)
        return moderation_flag

    @app.get(
        "/api/v1/messages/moderation/flags",
        response_model=MessageFlagListResponse,
        tags=["Message moderation"],
    )
    async def list_message_flags(
        _user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
        flag_status: Literal["open", "reviewed", "dismissed"] = "open",
        limit: int = 100,
    ) -> MessageFlagListResponse:
        if not 1 <= limit <= 100:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Flag limit must be between 1 and 100",
            )
        base_query = moderation_flag_query(session)
        base_query = base_query.where(base_query.selected_columns.status == flag_status)
        count_result = await session.execute(
            select(func.count()).select_from(
                base_query.order_by(None).subquery()
            )
        )
        result = await session.execute(
            base_query.order_by(base_query.selected_columns.created_at.desc()).limit(limit)
        )
        return MessageFlagListResponse(
            items=[MessageFlagResponse(**dict(row)) for row in result.mappings()],
            total=count_result.scalar_one(),
        )

    @app.patch(
        "/api/v1/messages/moderation/flags/{flag_id}",
        response_model=MessageFlagResponse,
        tags=["Message moderation"],
    )
    async def update_message_flag(
        flag_id: str,
        request: MessageFlagUpdate,
        user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> MessageFlagResponse:
        flag_table = tenant_messaging_tables(session)[2]
        result = await session.execute(
            update(flag_table)
            .where(flag_table.c.id == flag_id, flag_table.c.status == "open")
            .values(
                status=request.status,
                reviewed_by_user_id=user.id,
                reviewed_at=datetime.now(UTC),
            )
        )
        if result.rowcount != 1:
            exists_result = await session.execute(
                select(flag_table.c.id).where(flag_table.c.id == flag_id)
            )
            if exists_result.scalar_one_or_none() is None:
                raise HTTPException(status_code=404, detail="Message flag not found")
            raise HTTPException(status_code=409, detail="Message flag has already been reviewed")
        await session.commit()
        return await get_moderation_flag(session, flag_id)

    @app.get(
        "/api/v1/parents/announcements",
        response_model=ParentAnnouncementListResponse,
        tags=["Parents"],
    )
    async def list_parent_announcements(
        _user: Annotated[TenantUser, Depends(require_guardian)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
        limit: int = 50,
    ) -> ParentAnnouncementListResponse:
        if not 1 <= limit <= 100:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Announcement limit must be between 1 and 100",
            )
        announcement_table = tenant_announcement_tables(session)[0]
        total_result = await session.execute(
            select(func.count())
            .select_from(announcement_table)
            .where(announcement_table.c.status == "published")
        )
        rows_result = await session.execute(
            select(
                announcement_table.c.id,
                announcement_table.c.title,
                announcement_table.c.body,
                announcement_table.c.created_at,
                announcement_table.c.published_at,
            )
            .where(announcement_table.c.status == "published")
            .order_by(announcement_table.c.published_at.desc())
            .limit(limit)
        )
        return ParentAnnouncementListResponse(
            items=[
                ParentAnnouncementResponse(**dict(row))
                for row in rows_result.mappings()
            ],
            total=total_result.scalar_one(),
        )

    @app.get(
        "/api/v1/parents/billing/settings",
        response_model=ParentBillingSettingsResponse,
        tags=["Parents"],
    )
    async def get_parent_billing_settings(
        _user: Annotated[TenantUser, Depends(require_guardian)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> ParentBillingSettingsResponse:
        _table, settings = await billing_settings_for(session)
        response = billing_settings_response(settings)
        return ParentBillingSettingsResponse(
            provider=response.provider,
            currency=response.currency,
            configured=response.configured,
        )

    @app.get(
        "/api/v1/parents/invoices",
        response_model=InvoiceListResponse,
        tags=["Parents"],
    )
    async def list_parent_invoices(
        user: Annotated[TenantUser, Depends(require_guardian)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> InvoiceListResponse:
        invoice_table = tenant_billing_tables(session)[2]
        result = await session.execute(
            invoice_list_statement(
                session,
                invoice_table,
                guardian_email=user.email,
            ).order_by(
                invoice_table.c.issued_at.desc()
            )
        )
        rows = result.mappings().all()
        return InvoiceListResponse(
            items=await invoice_responses(session, rows),
            total=len(rows),
        )

    @app.post(
        "/api/v1/students/{student_id}/guardian-invitation",
        response_model=ActionMessage,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["Parents"],
    )
    async def invite_student_guardian(
        student_id: str,
        tenant: Annotated[Tenant, Depends(get_request_tenant)],
        user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> ActionMessage:
        require_email_configuration()
        student_table = tenant_students_table(session)
        student_result = await session.execute(
            select(
                student_table.c.id,
                student_table.c.first_name,
                student_table.c.last_name,
                student_table.c.guardian_name,
                student_table.c.guardian_email,
            ).where(
                student_table.c.id == student_id,
                student_table.c.is_active.is_(True),
            )
        )
        student = student_result.mappings().one_or_none()
        if student is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Active student not found",
            )
        if not student["guardian_email"]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Add a guardian email to the student record before inviting them",
            )

        guardian_email = student["guardian_email"].strip().lower()
        existing_user = await find_tenant_user(session, email=guardian_email)
        if existing_user is not None and existing_user.is_active:
            if existing_user.role == "guardian":
                return ActionMessage(message="Guardian already has parent portal access")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An active non-guardian account already uses this email",
            )
        if existing_user is not None and existing_user.role != "guardian":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A non-guardian account already uses this email",
            )

        guardian_id = existing_user.id if existing_user is not None else uuid.uuid4().hex
        invitation_token = make_action_token()
        try:
            invitation_link = action_url(
                tenant.identifier,
                "/invitation/accept",
                invitation_token,
            )
        except ValueError as error:
            logger.error("Invalid public app URL for guardian invitation: %s", error)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Invitation links are not configured correctly",
            ) from error

        user_table, _ = tenant_tables(session)
        try:
            if existing_user is None:
                await session.execute(
                    insert(user_table).values(
                        id=guardian_id,
                        email=guardian_email,
                        password_hash=DUMMY_PASSWORD_HASH,
                        role="guardian",
                        is_active=False,
                    )
                )
            else:
                await session.execute(
                    update(user_table)
                    .where(user_table.c.id == guardian_id)
                    .values(password_hash=DUMMY_PASSWORD_HASH, is_active=False)
                )
            await store_action_token(
                session,
                user_id=guardian_id,
                purpose="guardian_invitation",
                token=invitation_token,
                expires_at=datetime.now(UTC) + timedelta(hours=48),
            )
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The guardian account could not be created",
            ) from error

        try:
            await deliver_action_email(
                guardian_email,
                "Your Northstar parent portal invitation",
                (
                    f"{user.email} invited you to view {student['first_name']} "
                    f"{student['last_name']}'s school progress at {tenant.name}.\n\n"
                    f"Accept your invitation within 48 hours:\n{invitation_link}\n\n"
                    "If you were not expecting this invitation, contact the school."
                ),
            )
        except (OSError, smtplib.SMTPException) as error:
            logger.exception("Failed to deliver guardian invitation")
            await revoke_action_token(
                session,
                token=invitation_token,
                purpose="guardian_invitation",
            )
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Guardian invitation email could not be delivered",
            ) from error

        return ActionMessage(message="Parent portal invitation email sent")

    @app.post(
        "/api/v1/teachers/invitations",
        response_model=ActionMessage,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["Teachers"],
    )
    async def invite_teacher(
        payload: TeacherInvitationRequest,
        tenant: Annotated[Tenant, Depends(get_request_tenant)],
        user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> ActionMessage:
        require_email_configuration()
        user_table, _ = tenant_tables(session)
        _, _, class_table, _ = tenant_academic_tables(session)
        assignment_table = tenant_teacher_assignment_table(session)
        valid_classes = await session.execute(
            select(class_table.c.id).where(
                class_table.c.id.in_(payload.class_section_ids),
                class_table.c.is_active.is_(True),
            )
        )
        if {row[0] for row in valid_classes} != set(payload.class_section_ids):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Every assigned class must exist and be active",
            )

        existing_user = await find_tenant_user(session, email=payload.email)
        if existing_user is not None and (
            existing_user.is_active or existing_user.role != "teacher"
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with this email already exists",
            )

        teacher_id = existing_user.id if existing_user is not None else uuid.uuid4().hex
        invitation_token = make_action_token()
        try:
            invitation_link = action_url(
                tenant.identifier,
                "/invitation/accept",
                invitation_token,
            )
        except ValueError as error:
            logger.error("Invalid public app URL for teacher invitation: %s", error)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Invitation links are not configured correctly",
            ) from error

        try:
            if existing_user is None:
                await session.execute(
                    insert(user_table).values(
                        id=teacher_id,
                        email=payload.email,
                        password_hash=DUMMY_PASSWORD_HASH,
                        role="teacher",
                        is_active=False,
                    )
                )
            else:
                await session.execute(
                    update(user_table)
                    .where(user_table.c.id == teacher_id)
                    .values(password_hash=DUMMY_PASSWORD_HASH, is_active=False)
                )
            await session.execute(
                assignment_table.delete().where(
                    assignment_table.c.teacher_user_id == teacher_id
                )
            )
            for class_id in payload.class_section_ids:
                await session.execute(
                    insert(assignment_table).values(
                        id=uuid.uuid4().hex,
                        teacher_user_id=teacher_id,
                        class_section_id=class_id,
                        assigned_by_user_id=user.id,
                    )
                )
            await store_action_token(
                session,
                user_id=teacher_id,
                purpose="teacher_invitation",
                token=invitation_token,
                expires_at=datetime.now(UTC) + timedelta(hours=48),
            )
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The teacher account could not be created",
            ) from error

        try:
            await deliver_action_email(
                payload.email,
                "Your Northstar teacher invitation",
                (
                    f"You have been invited to teach at {tenant.name}.\n\n"
                    f"Accept your invitation within 48 hours:\n{invitation_link}\n\n"
                    "If you were not expecting this invitation, you can ignore this message."
                ),
            )
        except (OSError, smtplib.SMTPException) as error:
            logger.exception("Failed to deliver teacher invitation")
            await revoke_action_token(
                session,
                token=invitation_token,
                purpose="teacher_invitation",
            )
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Teacher invitation email could not be delivered",
            ) from error

        return ActionMessage(message="Teacher invitation email sent")

    @app.get(
        "/api/v1/teachers",
        response_model=list[TeacherResponse],
        tags=["Teachers"],
    )
    async def list_teachers(
        user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> list[TeacherResponse]:
        del user
        user_table, _ = tenant_tables(session)
        assignment_table = tenant_teacher_assignment_table(session)
        result = await session.execute(
            select(
                user_table.c.id,
                user_table.c.email,
                user_table.c.is_active,
                assignment_table.c.class_section_id,
            )
            .select_from(
                user_table.outerjoin(
                    assignment_table,
                    assignment_table.c.teacher_user_id == user_table.c.id,
                )
            )
            .where(user_table.c.role == "teacher")
            .order_by(user_table.c.email, assignment_table.c.class_section_id)
        )
        teachers: dict[str, TeacherResponse] = {}
        for row in result.mappings():
            teacher = teachers.get(row["id"])
            if teacher is None:
                teacher = TeacherResponse(
                    id=row["id"],
                    email=row["email"],
                    is_active=row["is_active"],
                    class_section_ids=[],
                )
                teachers[teacher.id] = teacher
            if row["class_section_id"] is not None:
                teacher.class_section_ids.append(row["class_section_id"])
        return list(teachers.values())

    @app.put(
        "/api/v1/teachers/{teacher_id}/classes",
        response_model=TeacherResponse,
        tags=["Teachers"],
    )
    async def update_teacher_classes(
        teacher_id: str,
        payload: TeacherAssignmentUpdate,
        user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> TeacherResponse:
        _, _, class_table, _ = tenant_academic_tables(session)
        assignment_table = tenant_teacher_assignment_table(session)
        teacher = await find_tenant_user(session, user_id=teacher_id)
        if teacher is None or teacher.role != "teacher":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Teacher not found",
            )
        if payload.class_section_ids:
            valid_classes = await session.execute(
                select(class_table.c.id).where(
                    class_table.c.id.in_(payload.class_section_ids),
                    class_table.c.is_active.is_(True),
                )
            )
            if {row[0] for row in valid_classes} != set(payload.class_section_ids):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Every assigned class must exist and be active",
                )

        try:
            await session.execute(
                assignment_table.delete().where(
                    assignment_table.c.teacher_user_id == teacher_id
                )
            )
            for class_id in payload.class_section_ids:
                await session.execute(
                    insert(assignment_table).values(
                        id=uuid.uuid4().hex,
                        teacher_user_id=teacher_id,
                        class_section_id=class_id,
                        assigned_by_user_id=user.id,
                    )
                )
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Teacher assignments could not be saved",
            ) from error

        return TeacherResponse(
            id=teacher.id,
            email=teacher.email,
            is_active=teacher.is_active,
            class_section_ids=payload.class_section_ids,
        )

    @app.get(
        "/api/v1/teacher/classes",
        response_model=list[ClassSectionResponse],
        tags=["Teachers"],
    )
    async def list_teacher_classes(
        user: Annotated[TenantUser, Depends(require_school_user)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> list[ClassSectionResponse]:
        if user.role != "teacher":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Teacher account required",
            )
        student_table, year_table, class_table, enrollment_table = tenant_academic_tables(session)
        assignment_table = tenant_teacher_assignment_table(session)
        result = await session.execute(
            select(
                class_table.c.id,
                class_table.c.name,
                class_table.c.academic_year_id,
                year_table.c.name.label("academic_year"),
                class_table.c.is_active,
                func.count(student_table.c.id).label("student_count"),
            )
            .select_from(
                assignment_table.join(
                    class_table,
                    assignment_table.c.class_section_id == class_table.c.id,
                )
                .join(
                    year_table,
                    class_table.c.academic_year_id == year_table.c.id,
                )
                .outerjoin(
                    enrollment_table,
                    and_(
                        enrollment_table.c.class_section_id == class_table.c.id,
                        enrollment_table.c.status == "active",
                    ),
                )
                .outerjoin(
                    student_table,
                    and_(
                        student_table.c.id == enrollment_table.c.student_id,
                        student_table.c.is_active.is_(True),
                    ),
                )
            )
            .where(
                assignment_table.c.teacher_user_id == user.id,
                class_table.c.is_active.is_(True),
            )
            .group_by(
                class_table.c.id,
                class_table.c.name,
                class_table.c.academic_year_id,
                year_table.c.name,
                class_table.c.is_active,
            )
            .order_by(year_table.c.name.desc(), class_table.c.name)
        )
        return [ClassSectionResponse(**dict(row)) for row in result.mappings()]

    async def staff_profile_responses(
        session: AsyncSession,
        profile_id: str | None = None,
    ) -> list[StaffProfileResponse]:
        profile_table, qualification_table, contract_table = tenant_staff_tables(session)
        user_table = tenant_users_table(session)
        profile_query = (
            select(
                profile_table.c.id,
                profile_table.c.linked_user_id,
                profile_table.c.staff_number,
                profile_table.c.first_name,
                profile_table.c.last_name,
                func.coalesce(profile_table.c.email, user_table.c.email).label("email"),
                profile_table.c.phone,
                profile_table.c.job_title,
                profile_table.c.department,
                profile_table.c.hire_date,
                profile_table.c.status,
                profile_table.c.notes,
            )
            .select_from(
                profile_table.outerjoin(
                    user_table,
                    profile_table.c.linked_user_id == user_table.c.id,
                )
            )
        )
        if profile_id is not None:
            profile_query = profile_query.where(profile_table.c.id == profile_id)
        profile_result = await session.execute(
            profile_query.order_by(
                profile_table.c.status,
                profile_table.c.last_name,
                profile_table.c.first_name,
            )
        )
        rows = profile_result.mappings().all()
        if not rows:
            return []
        profile_ids = [row["id"] for row in rows]
        qualification_result = await session.execute(
            select(
                qualification_table.c.id,
                qualification_table.c.staff_profile_id,
                qualification_table.c.title,
                qualification_table.c.institution,
                qualification_table.c.field_of_study,
                qualification_table.c.awarded_on,
                qualification_table.c.notes,
            )
            .where(qualification_table.c.staff_profile_id.in_(profile_ids))
            .order_by(qualification_table.c.awarded_on.desc(), qualification_table.c.title)
        )
        qualifications: dict[str, list[StaffQualificationResponse]] = {}
        for row in qualification_result.mappings():
            qualifications.setdefault(row["staff_profile_id"], []).append(
                StaffQualificationResponse(
                    id=row["id"],
                    title=row["title"],
                    institution=row["institution"],
                    field_of_study=row["field_of_study"],
                    awarded_on=row["awarded_on"],
                    notes=row["notes"],
                )
            )
        contract_result = await session.execute(
            select(
                contract_table.c.id,
                contract_table.c.staff_profile_id,
                contract_table.c.contract_type,
                contract_table.c.start_date,
                contract_table.c.end_date,
                contract_table.c.status,
                contract_table.c.reference,
                contract_table.c.notes,
            )
            .where(contract_table.c.staff_profile_id.in_(profile_ids))
            .order_by(contract_table.c.start_date.desc(), contract_table.c.created_at.desc())
        )
        contracts: dict[str, list[StaffContractResponse]] = {}
        for row in contract_result.mappings():
            contracts.setdefault(row["staff_profile_id"], []).append(
                StaffContractResponse(
                    id=row["id"],
                    contract_type=row["contract_type"],
                    start_date=row["start_date"],
                    end_date=row["end_date"],
                    status=row["status"],
                    reference=row["reference"],
                    notes=row["notes"],
                )
            )
        return [
            StaffProfileResponse(
                id=row["id"],
                linked_user_id=row["linked_user_id"],
                staff_number=row["staff_number"],
                first_name=row["first_name"],
                last_name=row["last_name"],
                email=row["email"],
                phone=row["phone"],
                job_title=row["job_title"],
                department=row["department"],
                hire_date=row["hire_date"],
                status=row["status"],
                notes=row["notes"],
                qualifications=qualifications.get(row["id"], []),
                contracts=contracts.get(row["id"], []),
            )
            for row in rows
        ]

    @app.get(
        "/api/v1/staff",
        response_model=list[StaffProfileResponse],
        tags=["Staff"],
    )
    async def list_staff_profiles(
        _user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> list[StaffProfileResponse]:
        return await staff_profile_responses(session)

    @app.post(
        "/api/v1/staff",
        response_model=StaffProfileResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Staff"],
    )
    async def create_staff_profile(
        request: StaffProfileCreate,
        _user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> StaffProfileResponse:
        profile_table = tenant_staff_tables(session)[0]
        if request.linked_user_id is not None:
            linked_user = await find_tenant_user(session, user_id=request.linked_user_id)
            if linked_user is None or linked_user.role != "teacher":
                raise HTTPException(
                    status_code=422,
                    detail="A linked account must be an existing teacher in this school",
                )
            if request.email is not None and request.email != linked_user.email.lower():
                raise HTTPException(
                    status_code=422,
                    detail="Staff email must match the linked teacher account",
                )
        profile_id = uuid.uuid4().hex
        try:
            await session.execute(
                insert(profile_table).values(
                    id=profile_id,
                    linked_user_id=request.linked_user_id,
                    staff_number=request.staff_number,
                    first_name=request.first_name,
                    last_name=request.last_name,
                    email=request.email,
                    phone=request.phone,
                    job_title=request.job_title,
                    department=request.department,
                    hire_date=request.hire_date,
                    status="active",
                    notes=request.notes,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            raise HTTPException(
                status_code=409,
                detail="Staff number or linked teacher already has a profile",
            ) from error
        return (await staff_profile_responses(session, profile_id))[0]

    @app.patch(
        "/api/v1/staff/{profile_id}",
        response_model=StaffProfileResponse,
        tags=["Staff"],
    )
    async def update_staff_profile(
        profile_id: str,
        request: StaffProfileUpdate,
        _user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> StaffProfileResponse:
        profile_table = tenant_staff_tables(session)[0]
        changes = request.model_dump(exclude_unset=True)
        if not changes:
            raise HTTPException(status_code=422, detail="Provide at least one profile field to update")
        exists_result = await session.execute(
            select(profile_table.c.id, profile_table.c.linked_user_id).where(
                profile_table.c.id == profile_id
            )
        )
        profile = exists_result.mappings().one_or_none()
        if profile is None:
            raise HTTPException(status_code=404, detail="Staff profile not found")
        if "email" in changes and profile["linked_user_id"] is not None:
            linked_user = await find_tenant_user(session, user_id=profile["linked_user_id"])
            if linked_user is None or changes["email"] not in {None, linked_user.email.lower()}:
                raise HTTPException(
                    status_code=422,
                    detail="Staff email must match the linked teacher account",
                )
        changes["updated_at"] = datetime.now(UTC)
        await session.execute(
            update(profile_table).where(profile_table.c.id == profile_id).values(**changes)
        )
        await session.commit()
        return (await staff_profile_responses(session, profile_id))[0]

    @app.post(
        "/api/v1/staff/{profile_id}/qualifications",
        response_model=StaffQualificationResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Staff"],
    )
    async def add_staff_qualification(
        profile_id: str,
        request: StaffQualificationCreate,
        _user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> StaffQualificationResponse:
        profile_table, qualification_table, _contract_table = tenant_staff_tables(session)
        exists_result = await session.execute(
            select(profile_table.c.id).where(profile_table.c.id == profile_id)
        )
        if exists_result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Staff profile not found")
        qualification_id = uuid.uuid4().hex
        await session.execute(
            insert(qualification_table).values(
                id=qualification_id,
                staff_profile_id=profile_id,
                title=request.title,
                institution=request.institution,
                field_of_study=request.field_of_study,
                awarded_on=request.awarded_on,
                notes=request.notes,
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()
        return StaffQualificationResponse(id=qualification_id, **request.model_dump())

    @app.post(
        "/api/v1/staff/{profile_id}/contracts",
        response_model=StaffContractResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Staff"],
    )
    async def add_staff_contract(
        profile_id: str,
        request: StaffContractCreate,
        _user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> StaffContractResponse:
        profile_table, _qualification_table, contract_table = tenant_staff_tables(session)
        exists_result = await session.execute(
            select(profile_table.c.id).where(profile_table.c.id == profile_id)
        )
        if exists_result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Staff profile not found")
        contract_id = uuid.uuid4().hex
        await session.execute(
            insert(contract_table).values(
                id=contract_id,
                staff_profile_id=profile_id,
                **request.model_dump(),
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()
        return StaffContractResponse(id=contract_id, **request.model_dump())

    @app.patch(
        "/api/v1/staff/{profile_id}/contracts/{contract_id}",
        response_model=StaffContractResponse,
        tags=["Staff"],
    )
    async def update_staff_contract(
        profile_id: str,
        contract_id: str,
        request: StaffContractStatusUpdate,
        _user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> StaffContractResponse:
        contract_table = tenant_staff_tables(session)[2]
        result = await session.execute(
            update(contract_table)
            .where(
                contract_table.c.id == contract_id,
                contract_table.c.staff_profile_id == profile_id,
            )
            .values(status=request.status)
        )
        if result.rowcount != 1:
            raise HTTPException(status_code=404, detail="Staff contract not found")
        await session.commit()
        response_result = await session.execute(
            select(
                contract_table.c.id,
                contract_table.c.contract_type,
                contract_table.c.start_date,
                contract_table.c.end_date,
                contract_table.c.status,
                contract_table.c.reference,
                contract_table.c.notes,
            ).where(contract_table.c.id == contract_id)
        )
        return StaffContractResponse(**dict(response_result.mappings().one()))

    async def timetable_entry_responses(
        session: AsyncSession,
        *,
        teacher_id: str | None = None,
    ) -> list[TimetableEntryResponse]:
        timetable_table = tenant_timetable_table(session)
        _student_table, _year_table, class_table, _enrollment_table = tenant_academic_tables(session)
        assignment_table = tenant_teacher_assignment_table(session)
        user_table = tenant_users_table(session)
        query = (
            select(
                timetable_table.c.id,
                timetable_table.c.class_section_id,
                class_table.c.name.label("class_name"),
                timetable_table.c.teacher_user_id.label("teacher_id"),
                user_table.c.email.label("teacher_email"),
                timetable_table.c.subject,
                timetable_table.c.room,
                timetable_table.c.weekday,
                timetable_table.c.start_time,
                timetable_table.c.end_time,
            )
            .select_from(
                timetable_table.join(
                    class_table,
                    and_(
                        class_table.c.id == timetable_table.c.class_section_id,
                        class_table.c.is_active.is_(True),
                    ),
                )
                .join(
                    assignment_table,
                    and_(
                        assignment_table.c.class_section_id == timetable_table.c.class_section_id,
                        assignment_table.c.teacher_user_id == timetable_table.c.teacher_user_id,
                    ),
                )
                .join(
                    user_table,
                    and_(
                        user_table.c.id == timetable_table.c.teacher_user_id,
                        user_table.c.role == "teacher",
                        user_table.c.is_active.is_(True),
                    ),
                )
            )
        )
        if teacher_id is not None:
            query = query.where(timetable_table.c.teacher_user_id == teacher_id)
        result = await session.execute(
            query.order_by(timetable_table.c.weekday, timetable_table.c.start_time, class_table.c.name)
        )
        return [TimetableEntryResponse(**dict(row)) for row in result.mappings()]

    async def validate_timetable_entry(
        session: AsyncSession,
        payload: TimetableEntryCreate,
        *,
        exclude_id: str | None = None,
    ) -> None:
        _student_table, _year_table, class_table, _enrollment_table = tenant_academic_tables(session)
        assignment_table = tenant_teacher_assignment_table(session)
        user_table = tenant_users_table(session)
        eligible_result = await session.execute(
            select(class_table.c.id)
            .select_from(
                class_table.join(
                    assignment_table,
                    and_(
                        assignment_table.c.class_section_id == class_table.c.id,
                        assignment_table.c.teacher_user_id == payload.teacher_id,
                    ),
                )
                .join(
                    user_table,
                    and_(
                        user_table.c.id == assignment_table.c.teacher_user_id,
                        user_table.c.role == "teacher",
                        user_table.c.is_active.is_(True),
                    ),
                )
            )
            .where(
                class_table.c.id == payload.class_section_id,
                class_table.c.is_active.is_(True),
            )
            .limit(1)
        )
        if eligible_result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=422,
                detail="Choose an active class and a teacher assigned to that class",
            )

        timetable_table = tenant_timetable_table(session)
        conflicts_query = select(
            timetable_table.c.class_section_id,
            timetable_table.c.teacher_user_id,
            timetable_table.c.room,
        ).where(
            timetable_table.c.weekday == payload.weekday,
            timetable_table.c.start_time < payload.end_time,
            timetable_table.c.end_time > payload.start_time,
        )
        if exclude_id is not None:
            conflicts_query = conflicts_query.where(timetable_table.c.id != exclude_id)
        conflicts_result = await session.execute(conflicts_query)
        for conflict in conflicts_result.mappings():
            if conflict["class_section_id"] == payload.class_section_id:
                raise HTTPException(status_code=409, detail="This class already has a lesson during that period")
            if conflict["teacher_user_id"] == payload.teacher_id:
                raise HTTPException(status_code=409, detail="This teacher already has a lesson during that period")
            if (
                payload.room
                and conflict["room"]
                and conflict["room"].casefold() == payload.room.casefold()
            ):
                raise HTTPException(status_code=409, detail="This room is already booked during that period")

    @app.get(
        "/api/v1/timetable",
        response_model=list[TimetableEntryResponse],
        tags=["Timetable"],
    )
    async def list_timetable(
        _user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> list[TimetableEntryResponse]:
        return await timetable_entry_responses(session)

    @app.post(
        "/api/v1/timetable",
        response_model=TimetableEntryResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Timetable"],
    )
    async def create_timetable_entry(
        payload: TimetableEntryCreate,
        _user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> TimetableEntryResponse:
        await validate_timetable_entry(session, payload)
        timetable_table = tenant_timetable_table(session)
        entry_id = uuid.uuid4().hex
        await session.execute(
            insert(timetable_table).values(
                id=entry_id,
                class_section_id=payload.class_section_id,
                teacher_user_id=payload.teacher_id,
                subject=payload.subject,
                room=payload.room,
                weekday=payload.weekday,
                start_time=payload.start_time,
                end_time=payload.end_time,
            )
        )
        await session.commit()
        entries = await timetable_entry_responses(session)
        saved_entry = next((entry for entry in entries if entry.id == entry_id), None)
        if saved_entry is None:
            raise HTTPException(status_code=409, detail="Timetable entry is no longer assigned to an active class")
        return saved_entry

    @app.put(
        "/api/v1/timetable/{entry_id}",
        response_model=TimetableEntryResponse,
        tags=["Timetable"],
    )
    async def update_timetable_entry(
        entry_id: str,
        payload: TimetableEntryCreate,
        _user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> TimetableEntryResponse:
        timetable_table = tenant_timetable_table(session)
        exists_result = await session.execute(
            select(timetable_table.c.id).where(timetable_table.c.id == entry_id)
        )
        if exists_result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Timetable entry not found")
        await validate_timetable_entry(session, payload, exclude_id=entry_id)
        await session.execute(
            update(timetable_table)
            .where(timetable_table.c.id == entry_id)
            .values(
                class_section_id=payload.class_section_id,
                teacher_user_id=payload.teacher_id,
                subject=payload.subject,
                room=payload.room,
                weekday=payload.weekday,
                start_time=payload.start_time,
                end_time=payload.end_time,
            )
        )
        await session.commit()
        entries = await timetable_entry_responses(session)
        saved_entry = next((entry for entry in entries if entry.id == entry_id), None)
        if saved_entry is None:
            raise HTTPException(status_code=409, detail="Timetable entry is no longer assigned to an active class")
        return saved_entry

    @app.delete(
        "/api/v1/timetable/{entry_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["Timetable"],
    )
    async def delete_timetable_entry(
        entry_id: str,
        _user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> None:
        timetable_table = tenant_timetable_table(session)
        result = await session.execute(
            timetable_table.delete().where(timetable_table.c.id == entry_id)
        )
        if result.rowcount != 1:
            raise HTTPException(status_code=404, detail="Timetable entry not found")
        await session.commit()

    @app.get(
        "/api/v1/teacher/timetable",
        response_model=list[TimetableEntryResponse],
        tags=["Timetable"],
    )
    async def list_teacher_timetable(
        user: Annotated[TenantUser, Depends(require_school_user)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> list[TimetableEntryResponse]:
        if user.role != "teacher":
            raise HTTPException(status_code=403, detail="Teacher account required")
        return await timetable_entry_responses(session, teacher_id=user.id)

    @app.get(
        "/api/v1/classes/{class_section_id}/attendance-roster",
        response_model=AttendanceRosterResponse,
        tags=["Attendance"],
    )
    async def get_attendance_roster(
        class_section_id: str,
        attendance_date: date,
        user: Annotated[TenantUser, Depends(require_school_user)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> AttendanceRosterResponse:
        await require_class_access(session, user, class_section_id)
        student_table, year_table, class_table, enrollment_table = tenant_academic_tables(session)
        attendance_table = tenant_attendance_table(session)
        query = (
            select(
                class_table.c.id.label("class_section_id"),
                class_table.c.name.label("class_name"),
                year_table.c.name.label("academic_year"),
                student_table.c.id.label("student_id"),
                student_table.c.admission_number,
                student_table.c.first_name,
                student_table.c.last_name,
                attendance_table.c.status.label("attendance_status"),
                attendance_table.c.client_timestamp,
            )
            .select_from(
                class_table.join(
                    year_table,
                    class_table.c.academic_year_id == year_table.c.id,
                )
                .join(
                    enrollment_table,
                    and_(
                        enrollment_table.c.class_section_id == class_table.c.id,
                        enrollment_table.c.status == "active",
                        enrollment_table.c.enrolled_at <= attendance_date,
                    ),
                )
                .join(
                    student_table,
                    and_(
                        student_table.c.id == enrollment_table.c.student_id,
                        student_table.c.is_active.is_(True),
                    ),
                )
                .outerjoin(
                    attendance_table,
                    and_(
                        attendance_table.c.student_id == student_table.c.id,
                        attendance_table.c.class_section_id == class_table.c.id,
                        attendance_table.c.attendance_date == attendance_date,
                    ),
                )
            )
            .where(
                class_table.c.id == class_section_id,
                class_table.c.is_active.is_(True),
            )
            .order_by(student_table.c.last_name, student_table.c.first_name)
        )
        result = await session.execute(query)
        rows = list(result.mappings())
        if not rows:
            class_result = await session.execute(
                select(class_table.c.id).where(
                    class_table.c.id == class_section_id,
                    class_table.c.is_active.is_(True),
                )
            )
            if class_result.scalar_one_or_none() is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Class not found",
                )
            class_metadata = await session.execute(
                select(
                    class_table.c.id.label("class_section_id"),
                    class_table.c.name.label("class_name"),
                    year_table.c.name.label("academic_year"),
                )
                .select_from(
                    class_table.join(
                        year_table,
                        class_table.c.academic_year_id == year_table.c.id,
                    )
                )
                .where(class_table.c.id == class_section_id)
            )
            class_row = class_metadata.mappings().one()
            return AttendanceRosterResponse(
                **dict(class_row),
                attendance_date=attendance_date,
                students=[],
            )

        first_row = rows[0]
        return AttendanceRosterResponse(
            class_section_id=first_row["class_section_id"],
            class_name=first_row["class_name"],
            academic_year=first_row["academic_year"],
            attendance_date=attendance_date,
            students=[
                AttendanceRosterEntry(
                    student_id=row["student_id"],
                    admission_number=row["admission_number"],
                    first_name=row["first_name"],
                    last_name=row["last_name"],
                    attendance_status=row["attendance_status"],
                    client_timestamp=row["client_timestamp"],
                )
                for row in rows
            ],
        )

    @app.post(
        "/api/v1/attendance/sync",
        response_model=AttendanceSyncResponse,
        tags=["Attendance"],
    )
    async def sync_attendance(
        payload: AttendanceSyncRequest,
        user: Annotated[TenantUser, Depends(require_school_user)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> AttendanceSyncResponse:
        student_table, _, class_table, enrollment_table = tenant_academic_tables(session)
        attendance_table = tenant_attendance_table(session)
        dialect_name = session.bind.dialect.name if session.bind is not None else ""
        if dialect_name == "sqlite":
            dialect_insert = sqlite_insert
        elif dialect_name == "postgresql":
            dialect_insert = postgresql_insert
        else:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Attendance sync requires PostgreSQL or SQLite",
            )

        results: list[AttendanceSyncResult] = []
        now = datetime.now(UTC)
        try:
            for entry in payload.entries:
                await require_class_access(session, user, entry.class_section_id)
                local_id = str(entry.local_id)
                existing_local = await session.execute(
                    select(attendance_table.c.local_id).where(
                        attendance_table.c.local_id == local_id
                    )
                )
                if existing_local.scalar_one_or_none() is not None:
                    results.append(
                        AttendanceSyncResult(local_id=entry.local_id, status="duplicate")
                    )
                    continue

                eligible = await session.execute(
                    select(enrollment_table.c.id)
                    .select_from(
                        enrollment_table.join(
                            student_table,
                            enrollment_table.c.student_id == student_table.c.id,
                        ).join(
                            class_table,
                            enrollment_table.c.class_section_id == class_table.c.id,
                        )
                    )
                    .where(
                        enrollment_table.c.student_id == entry.student_id,
                        enrollment_table.c.class_section_id == entry.class_section_id,
                        enrollment_table.c.status == "active",
                        enrollment_table.c.enrolled_at <= entry.attendance_date,
                        student_table.c.is_active.is_(True),
                        class_table.c.is_active.is_(True),
                    )
                )
                if eligible.scalar_one_or_none() is None:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=(
                            "Attendance can only be marked for an active student "
                            "enrolled in the selected class on that date"
                        ),
                    )

                statement = dialect_insert(attendance_table).values(
                    id=uuid.uuid4().hex,
                    student_id=entry.student_id,
                    class_section_id=entry.class_section_id,
                    attendance_date=entry.attendance_date,
                    status=entry.status,
                    marked_by_user_id=user.id,
                    marked_at=now,
                    local_id=local_id,
                    client_timestamp=entry.client_timestamp,
                )
                excluded = statement.excluded
                statement = statement.on_conflict_do_update(
                    index_elements=[
                        attendance_table.c.student_id,
                        attendance_table.c.class_section_id,
                        attendance_table.c.attendance_date,
                    ],
                    set_={
                        "status": excluded.status,
                        "marked_by_user_id": excluded.marked_by_user_id,
                        "marked_at": excluded.marked_at,
                        "local_id": excluded.local_id,
                        "client_timestamp": excluded.client_timestamp,
                    },
                    where=(
                        excluded.client_timestamp
                        > attendance_table.c.client_timestamp
                    ),
                )
                result = await session.execute(statement)
                results.append(
                    AttendanceSyncResult(
                        local_id=entry.local_id,
                        status="synced" if result.rowcount == 1 else "duplicate",
                    )
                )

            await session.commit()
        except HTTPException:
            await session.rollback()
            raise
        except IntegrityError as error:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Attendance sync conflicts with an existing record",
            ) from error
        return AttendanceSyncResponse(results=results)

    @app.get(
        "/api/v1/academic-years",
        response_model=list[AcademicYearResponse],
        tags=["Classes and enrollments"],
    )
    async def list_academic_years(
        user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> list[AcademicYearResponse]:
        del user
        _, year_table, class_table, _ = tenant_academic_tables(session)
        result = await session.execute(
            select(
                year_table.c.id,
                year_table.c.name,
                year_table.c.start_date,
                year_table.c.end_date,
                year_table.c.is_current,
                func.count(class_table.c.id).label("class_count"),
            )
            .select_from(
                year_table.outerjoin(
                    class_table,
                    class_table.c.academic_year_id == year_table.c.id,
                )
            )
            .group_by(
                year_table.c.id,
                year_table.c.name,
                year_table.c.start_date,
                year_table.c.end_date,
                year_table.c.is_current,
            )
            .order_by(year_table.c.name.desc())
        )
        return [AcademicYearResponse(**dict(row)) for row in result.mappings()]

    @app.post(
        "/api/v1/academic-years",
        response_model=AcademicYearResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Classes and enrollments"],
    )
    async def create_academic_year(
        payload: AcademicYearCreate,
        user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> AcademicYearResponse:
        del user
        if (
            payload.start_date is not None
            and payload.end_date is not None
            and payload.start_date >= payload.end_date
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Academic year end date must be after its start date",
            )

        _, year_table, _, _ = tenant_academic_tables(session)
        year_id = uuid.uuid4().hex
        try:
            if payload.is_current:
                await session.execute(
                    update(year_table).values(is_current=False)
                )
            await session.execute(
                insert(year_table).values(
                    id=year_id,
                    name=payload.name,
                    start_date=payload.start_date,
                    end_date=payload.end_date,
                    is_current=payload.is_current,
                )
            )
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This academic year already exists",
            ) from error
        return AcademicYearResponse(
            id=year_id,
            name=payload.name,
            start_date=payload.start_date,
            end_date=payload.end_date,
            is_current=payload.is_current,
            class_count=0,
        )

    @app.get(
        "/api/v1/academic-years/{academic_year_id}/terms",
        response_model=list[AcademicTermResponse],
        tags=["Academic terms"],
    )
    async def list_academic_terms(
        academic_year_id: str,
        user: Annotated[TenantUser, Depends(require_school_user)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> list[AcademicTermResponse]:
        del user
        _, year_table, _, _ = tenant_academic_tables(session)
        term_table = tenant_academic_term_table(session)
        result = await session.execute(
            select(
                term_table.c.id,
                term_table.c.academic_year_id,
                year_table.c.name.label("academic_year"),
                term_table.c.name,
                term_table.c.start_date,
                term_table.c.end_date,
            )
            .select_from(
                term_table.join(
                    year_table,
                    term_table.c.academic_year_id == year_table.c.id,
                )
            )
            .where(term_table.c.academic_year_id == academic_year_id)
            .order_by(term_table.c.start_date, term_table.c.name)
        )
        return [AcademicTermResponse(**dict(row)) for row in result.mappings()]

    @app.post(
        "/api/v1/academic-years/{academic_year_id}/terms",
        response_model=AcademicTermResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Academic terms"],
    )
    async def create_academic_term(
        academic_year_id: str,
        payload: AcademicTermCreate,
        user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> AcademicTermResponse:
        del user
        _, year_table, _, _ = tenant_academic_tables(session)
        term_table = tenant_academic_term_table(session)
        year_result = await session.execute(
            select(year_table.c.id, year_table.c.name).where(
                year_table.c.id == academic_year_id
            )
        )
        year = year_result.mappings().one_or_none()
        if year is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Academic year not found",
            )
        if payload.end_date < payload.start_date:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Term end date must be on or after its start date",
            )
        overlap_result = await session.execute(
            select(term_table.c.id).where(
                term_table.c.academic_year_id == academic_year_id,
                term_table.c.start_date <= payload.end_date,
                term_table.c.end_date >= payload.start_date,
            )
        )
        if overlap_result.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Term dates cannot overlap another term in this academic year",
            )

        term_id = uuid.uuid4().hex
        try:
            await session.execute(
                insert(term_table).values(
                    id=term_id,
                    academic_year_id=academic_year_id,
                    name=payload.name,
                    start_date=payload.start_date,
                    end_date=payload.end_date,
                )
            )
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A term with this name already exists in the academic year",
            ) from error
        return AcademicTermResponse(
            id=term_id,
            academic_year_id=academic_year_id,
            academic_year=year["name"],
            name=payload.name,
            start_date=payload.start_date,
            end_date=payload.end_date,
        )

    @app.get(
        "/api/v1/classes",
        response_model=list[ClassSectionResponse],
        tags=["Classes and enrollments"],
    )
    async def list_class_sections(
        user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
        include_inactive: bool = False,
    ) -> list[ClassSectionResponse]:
        del user
        student_table, year_table, class_table, enrollment_table = tenant_academic_tables(session)
        query = (
            select(
                class_table.c.id,
                class_table.c.name,
                class_table.c.academic_year_id,
                year_table.c.name.label("academic_year"),
                class_table.c.is_active,
                func.count(student_table.c.id).label("student_count"),
            )
            .select_from(
                class_table.join(
                    year_table,
                    class_table.c.academic_year_id == year_table.c.id,
                ).outerjoin(
                    enrollment_table,
                    and_(
                        enrollment_table.c.class_section_id == class_table.c.id,
                        enrollment_table.c.status == "active",
                    ),
                ).outerjoin(
                    student_table,
                    and_(
                        student_table.c.id == enrollment_table.c.student_id,
                        student_table.c.is_active.is_(True),
                    ),
                )
            )
            .group_by(
                class_table.c.id,
                class_table.c.name,
                class_table.c.academic_year_id,
                year_table.c.name,
                class_table.c.is_active,
            )
            .order_by(year_table.c.name.desc(), class_table.c.name)
        )
        if not include_inactive:
            query = query.where(class_table.c.is_active.is_(True))
        result = await session.execute(query)
        return [
            ClassSectionResponse(**dict(row))
            for row in result.mappings()
        ]

    @app.post(
        "/api/v1/classes",
        response_model=ClassSectionResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Classes and enrollments"],
    )
    async def create_class_section(
        payload: ClassSectionCreate,
        user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> ClassSectionResponse:
        del user
        _, _, class_table, _ = tenant_academic_tables(session)
        class_id = uuid.uuid4().hex
        try:
            academic_year_id = await ensure_academic_year(
                session,
                name=payload.academic_year,
            )
            result = await session.execute(
                insert(class_table)
                .values(
                    id=class_id,
                    name=payload.name,
                    academic_year_id=academic_year_id,
                    is_active=True,
                )
                .returning(class_table)
            )
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This class already exists for the academic year",
            ) from error
        response = dict(result.mappings().one())
        response["academic_year"] = payload.academic_year
        response["student_count"] = 0
        return ClassSectionResponse(**response)

    @app.get(
        "/api/v1/classes/{class_section_id}/assessments",
        response_model=list[AssessmentResponse],
        tags=["Grades"],
    )
    async def list_assessments(
        class_section_id: str,
        user: Annotated[TenantUser, Depends(require_school_user)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
        term_id: str | None = None,
    ) -> list[AssessmentResponse]:
        await require_class_access(session, user, class_section_id)
        _, year_table, class_table, _ = tenant_academic_tables(session)
        assessment_table, grade_table = tenant_assessment_tables(session)
        term_table = tenant_academic_term_table(session)
        class_result = await session.execute(
            select(class_table.c.id, class_table.c.name, year_table.c.name.label("academic_year"))
            .select_from(
                class_table.join(
                    year_table,
                    class_table.c.academic_year_id == year_table.c.id,
                )
            )
            .where(class_table.c.id == class_section_id, class_table.c.is_active.is_(True))
        )
        class_row = class_result.mappings().one_or_none()
        if class_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Class not found",
            )

        assessment_query = (
            select(
                assessment_table.c.id,
                assessment_table.c.academic_term_id.label("term_id"),
                term_table.c.name.label("term_name"),
                assessment_table.c.class_section_id,
                class_table.c.name.label("class_name"),
                year_table.c.name.label("academic_year"),
                assessment_table.c.subject,
                assessment_table.c.title,
                assessment_table.c.assessment_date,
                assessment_table.c.max_score,
                func.count(grade_table.c.id).label("graded_count"),
            )
            .select_from(
                assessment_table.join(
                    class_table,
                    assessment_table.c.class_section_id == class_table.c.id,
                )
                .join(
                    year_table,
                    class_table.c.academic_year_id == year_table.c.id,
                )
                .outerjoin(
                    term_table,
                    assessment_table.c.academic_term_id == term_table.c.id,
                )
                .outerjoin(
                    grade_table,
                    grade_table.c.assessment_id == assessment_table.c.id,
                )
            )
            .where(assessment_table.c.class_section_id == class_section_id)
            .group_by(
                assessment_table.c.id,
                assessment_table.c.academic_term_id,
                term_table.c.name,
                assessment_table.c.class_section_id,
                class_table.c.name,
                year_table.c.name,
                assessment_table.c.subject,
                assessment_table.c.title,
                assessment_table.c.assessment_date,
                assessment_table.c.max_score,
            )
            .order_by(
                assessment_table.c.assessment_date.desc(),
                assessment_table.c.title,
            )
        )
        if term_id == "unassigned":
            assessment_query = assessment_query.where(
                assessment_table.c.academic_term_id.is_(None)
            )
        elif term_id is not None:
            assessment_query = assessment_query.where(
                assessment_table.c.academic_term_id == term_id
            )
        result = await session.execute(assessment_query)
        return [AssessmentResponse(**dict(row)) for row in result.mappings()]

    @app.post(
        "/api/v1/classes/{class_section_id}/assessments",
        response_model=AssessmentResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Grades"],
    )
    async def create_assessment(
        class_section_id: str,
        payload: AssessmentCreate,
        user: Annotated[TenantUser, Depends(require_school_user)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> AssessmentResponse:
        await require_class_access(session, user, class_section_id)
        _, year_table, class_table, _ = tenant_academic_tables(session)
        assessment_table, _ = tenant_assessment_tables(session)
        term_table = tenant_academic_term_table(session)
        class_result = await session.execute(
            select(
                class_table.c.id,
                class_table.c.academic_year_id,
                class_table.c.name.label("class_name"),
                year_table.c.name.label("academic_year"),
            )
            .select_from(
                class_table.join(
                    year_table,
                    class_table.c.academic_year_id == year_table.c.id,
                )
            )
            .where(class_table.c.id == class_section_id, class_table.c.is_active.is_(True))
        )
        class_row = class_result.mappings().one_or_none()
        if class_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Class not found",
            )

        term_result = await session.execute(
            select(
                term_table.c.id,
                term_table.c.academic_year_id,
                term_table.c.name,
                term_table.c.start_date,
                term_table.c.end_date,
            ).where(term_table.c.id == payload.term_id)
        )
        term = term_result.mappings().one_or_none()
        if (
            term is None
            or term["academic_year_id"] != class_row["academic_year_id"]
            or not term["start_date"] <= payload.assessment_date <= term["end_date"]
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Select a term in the class academic year that contains the assessment date",
            )

        assessment_id = uuid.uuid4().hex
        try:
            await session.execute(
                insert(assessment_table).values(
                    id=assessment_id,
                    academic_term_id=payload.term_id,
                    class_section_id=class_section_id,
                    subject=payload.subject,
                    title=payload.title,
                    assessment_date=payload.assessment_date,
                    max_score=payload.max_score,
                    created_by_user_id=user.id,
                )
            )
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An assessment with this subject, title, and date already exists for the class",
            ) from error

        return AssessmentResponse(
            id=assessment_id,
            term_id=payload.term_id,
            term_name=term["name"],
            class_section_id=class_section_id,
            class_name=class_row["class_name"],
            academic_year=class_row["academic_year"],
            subject=payload.subject,
            title=payload.title,
            assessment_date=payload.assessment_date,
            max_score=payload.max_score,
            graded_count=0,
        )

    @app.get(
        "/api/v1/assessments/{assessment_id}/grades",
        response_model=AssessmentGradebookResponse,
        tags=["Grades"],
    )
    async def get_assessment_gradebook(
        assessment_id: str,
        user: Annotated[TenantUser, Depends(require_school_user)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> AssessmentGradebookResponse:
        student_table, year_table, class_table, enrollment_table = tenant_academic_tables(session)
        assessment_table, grade_table = tenant_assessment_tables(session)
        term_table = tenant_academic_term_table(session)
        assessment_result = await session.execute(
            select(
                assessment_table.c.id,
                assessment_table.c.academic_term_id.label("term_id"),
                term_table.c.name.label("term_name"),
                assessment_table.c.class_section_id,
                class_table.c.name.label("class_name"),
                year_table.c.name.label("academic_year"),
                assessment_table.c.subject,
                assessment_table.c.title,
                assessment_table.c.assessment_date,
                assessment_table.c.max_score,
            )
            .select_from(
                assessment_table.join(
                    class_table,
                    assessment_table.c.class_section_id == class_table.c.id,
                ).join(
                    year_table,
                    class_table.c.academic_year_id == year_table.c.id,
                ).outerjoin(
                    term_table,
                    assessment_table.c.academic_term_id == term_table.c.id,
                )
            )
            .where(assessment_table.c.id == assessment_id)
        )
        assessment_row = assessment_result.mappings().one_or_none()
        if assessment_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assessment not found",
            )
        await require_class_access(
            session,
            user,
            assessment_row["class_section_id"],
        )

        grade_rows = await session.execute(
            select(
                student_table.c.id.label("student_id"),
                student_table.c.admission_number,
                student_table.c.first_name,
                student_table.c.last_name,
                grade_table.c.score,
                and_(
                    student_table.c.is_active.is_(True),
                    enrollment_table.c.id.is_not(None),
                ).label("is_currently_enrolled"),
            )
            .select_from(
                student_table.outerjoin(
                    enrollment_table,
                    and_(
                        enrollment_table.c.student_id == student_table.c.id,
                        enrollment_table.c.class_section_id
                        == assessment_row["class_section_id"],
                        enrollment_table.c.status == "active",
                        enrollment_table.c.enrolled_at
                        <= assessment_row["assessment_date"],
                    ),
                ).outerjoin(
                    grade_table,
                    and_(
                        grade_table.c.student_id == student_table.c.id,
                        grade_table.c.assessment_id == assessment_id,
                    ),
                )
            )
            .where(
                or_(
                    grade_table.c.id.is_not(None),
                    and_(
                        enrollment_table.c.id.is_not(None),
                        student_table.c.is_active.is_(True),
                    ),
                )
            )
            .order_by(student_table.c.last_name, student_table.c.first_name)
        )
        students = [
            GradebookStudentResponse(**dict(row))
            for row in grade_rows.mappings()
        ]
        assessment = AssessmentResponse(
            **dict(assessment_row),
            graded_count=sum(student.score is not None for student in students),
        )
        return AssessmentGradebookResponse(assessment=assessment, students=students)

    @app.put(
        "/api/v1/assessments/{assessment_id}/grades",
        response_model=GradeSaveResponse,
        tags=["Grades"],
    )
    async def save_assessment_grades(
        assessment_id: str,
        payload: GradeSaveRequest,
        user: Annotated[TenantUser, Depends(require_school_user)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> GradeSaveResponse:
        student_table, _, _, enrollment_table = tenant_academic_tables(session)
        assessment_table, grade_table = tenant_assessment_tables(session)
        assessment_result = await session.execute(
            select(
                assessment_table.c.id,
                assessment_table.c.class_section_id,
                assessment_table.c.assessment_date,
                assessment_table.c.max_score,
            ).where(assessment_table.c.id == assessment_id)
        )
        assessment = assessment_result.mappings().one_or_none()
        if assessment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assessment not found",
            )
        await require_class_access(
            session,
            user,
            assessment["class_section_id"],
        )

        student_ids = [entry.student_id for entry in payload.entries]
        if student_ids:
            eligible_result = await session.execute(
                select(student_table.c.id)
                .select_from(
                    student_table.outerjoin(
                        enrollment_table,
                        and_(
                            enrollment_table.c.student_id == student_table.c.id,
                            enrollment_table.c.class_section_id
                            == assessment["class_section_id"],
                            enrollment_table.c.status == "active",
                            enrollment_table.c.enrolled_at
                            <= assessment["assessment_date"],
                        ),
                    ).outerjoin(
                        grade_table,
                        and_(
                            grade_table.c.student_id == student_table.c.id,
                            grade_table.c.assessment_id == assessment_id,
                        ),
                    )
                )
                .where(
                    student_table.c.id.in_(student_ids),
                    or_(
                        and_(
                            student_table.c.is_active.is_(True),
                            enrollment_table.c.id.is_not(None),
                        ),
                        grade_table.c.id.is_not(None),
                    ),
                )
            )
            eligible_ids = {row[0] for row in eligible_result}
            if eligible_ids != set(student_ids):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Every student must be enrolled in this class for the assessment date",
                )

        if any(
            entry.score is not None and entry.score > assessment["max_score"]
            for entry in payload.entries
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A grade cannot exceed the assessment maximum score",
            )

        updated_count = 0
        removed_count = 0
        now = datetime.now(UTC)
        try:
            for entry in payload.entries:
                existing_result = await session.execute(
                    select(grade_table.c.id).where(
                        grade_table.c.assessment_id == assessment_id,
                        grade_table.c.student_id == entry.student_id,
                    )
                )
                existing_id = existing_result.scalar_one_or_none()
                if entry.score is None:
                    if existing_id is not None:
                        await session.execute(
                            grade_table.delete().where(grade_table.c.id == existing_id)
                        )
                        removed_count += 1
                    continue
                if existing_id is None:
                    await session.execute(
                        insert(grade_table).values(
                            id=uuid.uuid4().hex,
                            assessment_id=assessment_id,
                            student_id=entry.student_id,
                            score=entry.score,
                            updated_at=now,
                        )
                    )
                else:
                    await session.execute(
                        update(grade_table)
                        .where(grade_table.c.id == existing_id)
                        .values(score=entry.score, updated_at=now)
                    )
                updated_count += 1
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The grades could not be saved because the record changed",
            ) from error

        return GradeSaveResponse(
            assessment_id=assessment_id,
            updated_count=updated_count,
            removed_count=removed_count,
        )

    @app.get(
        "/api/v1/classes/{class_section_id}/terms/{term_id}/report-cards",
        response_model=list[StudentReportCardResponse],
        tags=["Report cards"],
    )
    async def get_term_report_cards(
        class_section_id: str,
        term_id: str,
        user: Annotated[TenantUser, Depends(require_school_user)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
        student_id: str | None = None,
    ) -> list[StudentReportCardResponse]:
        student_table, year_table, class_table, enrollment_table = tenant_academic_tables(session)
        assessment_table, grade_table = tenant_assessment_tables(session)
        term_table = tenant_academic_term_table(session)
        attendance_table = tenant_attendance_table(session)
        if user.role == "guardian":
            if student_id is None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="A guardian may only view their own child's report card",
                )
            child_result = await session.execute(
                select(student_table.c.id).where(
                    student_table.c.id == student_id,
                    func.lower(student_table.c.guardian_email) == user.email.lower(),
                    student_table.c.is_active.is_(True),
                )
            )
            if child_result.scalar_one_or_none() is None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="This student is not linked to your guardian account",
                )
        else:
            await require_class_access(session, user, class_section_id)

        class_result = await session.execute(
            select(
                class_table.c.id,
                class_table.c.name.label("class_name"),
                class_table.c.academic_year_id,
                year_table.c.name.label("academic_year"),
            )
            .select_from(
                class_table.join(
                    year_table,
                    class_table.c.academic_year_id == year_table.c.id,
                )
            )
            .where(class_table.c.id == class_section_id, class_table.c.is_active.is_(True))
        )
        class_row = class_result.mappings().one_or_none()
        if class_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Class not found",
            )

        term_result = await session.execute(
            select(term_table).where(
                term_table.c.id == term_id,
                term_table.c.academic_year_id == class_row["academic_year_id"],
            )
        )
        term = term_result.mappings().one_or_none()
        if term is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Term not found for this class academic year",
            )

        grade_rows = await session.execute(
            select(
                student_table.c.id.label("student_id"),
                student_table.c.admission_number,
                student_table.c.first_name,
                student_table.c.last_name,
                assessment_table.c.subject,
                assessment_table.c.title,
                assessment_table.c.assessment_date,
                assessment_table.c.max_score,
                grade_table.c.score,
            )
            .select_from(
                student_table.outerjoin(
                    enrollment_table,
                    and_(
                        enrollment_table.c.student_id == student_table.c.id,
                        enrollment_table.c.class_section_id == class_section_id,
                        enrollment_table.c.status == "active",
                        enrollment_table.c.enrolled_at <= term["end_date"],
                    ),
                )
                .outerjoin(
                    assessment_table,
                    and_(
                        assessment_table.c.class_section_id == class_section_id,
                        or_(
                            assessment_table.c.academic_term_id == term_id,
                            and_(
                                assessment_table.c.academic_term_id.is_(None),
                                assessment_table.c.assessment_date
                                >= term["start_date"],
                                assessment_table.c.assessment_date
                                <= term["end_date"],
                            ),
                        ),
                    ),
                )
                .outerjoin(
                    grade_table,
                    and_(
                        grade_table.c.student_id == student_table.c.id,
                        grade_table.c.assessment_id == assessment_table.c.id,
                    ),
                )
            )
            .where(
                student_table.c.is_active.is_(True),
                or_(
                    enrollment_table.c.id.is_not(None),
                    grade_table.c.id.is_not(None),
                ),
                student_table.c.id == student_id if student_id else True,
            )
            .order_by(
                student_table.c.last_name,
                student_table.c.first_name,
                assessment_table.c.subject,
                assessment_table.c.assessment_date,
            )
        )

        student_records: dict[str, ReportCardAccumulator] = {}
        for row in grade_rows.mappings():
            student_id = row["student_id"]
            record = student_records.setdefault(
                student_id,
                {
                    "student_id": student_id,
                    "admission_number": row["admission_number"],
                    "first_name": row["first_name"],
                    "last_name": row["last_name"],
                    "assessments": [],
                    "subjects": {},
                },
            )
            score = row["score"]
            if score is None:
                continue
            max_score = row["max_score"]
            percentage = score / max_score * Decimal(100)
            assessment_result = ReportCardAssessment(
                subject=row["subject"],
                title=row["title"],
                assessment_date=row["assessment_date"],
                score=score,
                max_score=max_score,
                percentage=percentage,
            )
            record["assessments"].append(assessment_result)
            subject_values = record["subjects"].setdefault(
                row["subject"],
                {"score_total": Decimal(0), "max_score_total": Decimal(0), "count": 0},
            )
            subject_values["score_total"] += score
            subject_values["max_score_total"] += max_score
            subject_values["count"] += 1

        attendance_result = await session.execute(
            select(
                attendance_table.c.student_id,
                attendance_table.c.status,
            )
            .where(
                attendance_table.c.class_section_id == class_section_id,
                attendance_table.c.attendance_date >= term["start_date"],
                attendance_table.c.attendance_date <= term["end_date"],
                attendance_table.c.student_id.in_(student_records) if student_records else False,
            )
        )
        attendance_counts: dict[str, dict[str, int]] = {}
        for row in attendance_result.mappings():
            counts = attendance_counts.setdefault(
                row["student_id"],
                {"present": 0, "late": 0, "absent": 0, "excused": 0},
            )
            counts[row["status"]] += 1

        report_cards: list[StudentReportCardResponse] = []
        for record in student_records.values():
            student_id = record["student_id"]
            counts = attendance_counts.get(
                student_id,
                {"present": 0, "late": 0, "absent": 0, "excused": 0},
            )
            attendance_denominator = counts["present"] + counts["late"] + counts["absent"]
            attendance_rate = (
                Decimal(counts["present"] + counts["late"])
                / Decimal(attendance_denominator)
                * Decimal(100)
                if attendance_denominator
                else None
            )
            subject_results = []
            total_score = Decimal(0)
            total_max_score = Decimal(0)
            for subject, values in sorted(record["subjects"].items()):
                score_total = values["score_total"]
                max_score_total = values["max_score_total"]
                total_score += score_total
                total_max_score += max_score_total
                subject_results.append(
                    ReportCardSubjectResult(
                        subject=subject,
                        score_total=score_total,
                        max_score_total=max_score_total,
                        percentage=score_total / max_score_total * Decimal(100),
                        assessment_count=values["count"],
                    )
                )
            report_cards.append(
                StudentReportCardResponse(
                    student_id=student_id,
                    admission_number=record["admission_number"],
                    first_name=record["first_name"],
                    last_name=record["last_name"],
                    class_name=class_row["class_name"],
                    academic_year=class_row["academic_year"],
                    term_name=term["name"],
                    term_start_date=term["start_date"],
                    term_end_date=term["end_date"],
                    assessments=record["assessments"],
                    subjects=subject_results,
                    overall_percentage=(
                        total_score / total_max_score * Decimal(100)
                        if total_max_score
                        else None
                    ),
                    attendance=ReportCardAttendance(
                        **counts,
                        marked_days=attendance_denominator + counts["excused"],
                        attendance_rate=attendance_rate,
                    ),
                )
            )
        return report_cards

    @app.get(
        "/api/v1/parents/children",
        response_model=list[ParentChildResponse],
        tags=["Parents"],
    )
    async def list_parent_children(
        user: Annotated[TenantUser, Depends(require_guardian)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> list[ParentChildResponse]:
        student_table = tenant_students_table(session)
        result = await session.execute(
            select(
                student_table.c.id,
                student_table.c.admission_number,
                student_table.c.first_name,
                student_table.c.last_name,
                student_table.c.class_name,
                student_table.c.academic_year,
            )
            .where(
                func.lower(student_table.c.guardian_email) == user.email.lower(),
                student_table.c.is_active.is_(True),
            )
            .order_by(
                student_table.c.last_name,
                student_table.c.first_name,
                student_table.c.admission_number,
            )
        )
        return [
            ParentChildResponse(**dict(row))
            for row in result.mappings()
        ]

    @app.get(
        "/api/v1/parents/children/{student_id}/attendance",
        response_model=list[ParentAttendanceRecordResponse],
        tags=["Parents"],
    )
    async def get_parent_child_attendance(
        student_id: str,
        user: Annotated[TenantUser, Depends(require_guardian)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
        start_date: date | None = None,
        end_date: date | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[ParentAttendanceRecordResponse]:
        if offset < 0 or not 1 <= limit <= 500:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Attendance pagination values are invalid",
            )
        if start_date is not None and end_date is not None and start_date > end_date:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Attendance start date must not be after end date",
            )

        student_table, _, class_table, _ = tenant_academic_tables(session)
        child_result = await session.execute(
            select(student_table.c.id).where(
                student_table.c.id == student_id,
                func.lower(student_table.c.guardian_email) == user.email.lower(),
                student_table.c.is_active.is_(True),
            )
        )
        if child_result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Student not found",
            )

        attendance_table = tenant_attendance_table(session)
        filters = [attendance_table.c.student_id == student_id]
        if start_date is not None:
            filters.append(attendance_table.c.attendance_date >= start_date)
        if end_date is not None:
            filters.append(attendance_table.c.attendance_date <= end_date)
        records = await session.execute(
            select(
                attendance_table.c.attendance_date,
                class_table.c.name.label("class_name"),
                attendance_table.c.status,
            )
            .select_from(
                attendance_table.join(
                    class_table,
                    attendance_table.c.class_section_id == class_table.c.id,
                )
            )
            .where(*filters)
            .order_by(attendance_table.c.attendance_date.desc())
            .offset(offset)
            .limit(limit)
        )
        return [
            ParentAttendanceRecordResponse(**dict(row))
            for row in records.mappings()
        ]

    @app.get(
        "/api/v1/parents/children/{student_id}/report-cards",
        response_model=list[StudentReportCardResponse],
        tags=["Parents"],
    )
    async def get_parent_child_report_cards(
        student_id: str,
        user: Annotated[TenantUser, Depends(require_guardian)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> list[StudentReportCardResponse]:
        student_table, year_table, class_table, enrollment_table = tenant_academic_tables(session)
        term_table = tenant_academic_term_table(session)

        child_result = await session.execute(
            select(student_table.c.id).where(
                student_table.c.id == student_id,
                func.lower(student_table.c.guardian_email) == user.email.lower(),
                student_table.c.is_active.is_(True),
            )
        )
        if child_result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Student not found",
            )

        enrollment_result = await session.execute(
            select(
                class_table.c.id.label("class_section_id"),
                class_table.c.academic_year_id,
            )
            .select_from(
                enrollment_table.join(
                    class_table,
                    enrollment_table.c.class_section_id == class_table.c.id,
                )
            )
            .where(
                enrollment_table.c.student_id == student_id,
                enrollment_table.c.status == "active",
                class_table.c.is_active.is_(True),
            )
            .distinct()
        )
        report_cards: list[StudentReportCardResponse] = []
        for enrollment in enrollment_result.mappings():
            terms_result = await session.execute(
                select(term_table.c.id)
                .where(term_table.c.academic_year_id == enrollment["academic_year_id"])
                .order_by(term_table.c.start_date.desc())
            )
            for term_id in terms_result.scalars():
                report_cards.extend(
                    await get_term_report_cards(
                        enrollment["class_section_id"],
                        term_id,
                        user,
                        session,
                        student_id,
                    )
                )
        return report_cards

    @app.get(
        "/api/v1/students",
        response_model=StudentListResponse,
        tags=["Students"],
    )
    async def list_students(
        user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
        search: str | None = None,
        include_inactive: bool = False,
        offset: int = 0,
        limit: int = 100,
    ) -> StudentListResponse:
        del user
        if offset < 0 or not 1 <= limit <= 100:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Offset must be non-negative and limit must be between 1 and 100",
            )

        table = tenant_students_table(session)
        filters = []
        if not include_inactive:
            filters.append(table.c.is_active.is_(True))
        if search and search.strip():
            search_term = f"%{search.strip()}%"
            filters.append(
                table.c.first_name.ilike(search_term)
                | table.c.last_name.ilike(search_term)
                | table.c.admission_number.ilike(search_term)
                | table.c.class_name.ilike(search_term)
            )

        total_result = await session.execute(
            select(func.count()).select_from(table).where(*filters)
        )
        result = await session.execute(
            select(table)
            .where(*filters)
            .order_by(table.c.last_name, table.c.first_name, table.c.admission_number)
            .offset(offset)
            .limit(limit)
        )
        return StudentListResponse(
            items=[StudentResponse(**dict(row)) for row in result.mappings()],
            total=total_result.scalar_one(),
        )

    @app.post(
        "/api/v1/students",
        response_model=StudentResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Students"],
    )
    async def admit_student(
        payload: StudentAdmission,
        user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> StudentResponse:
        del user
        table = tenant_students_table(session)
        values = payload.model_dump()
        values["id"] = uuid.uuid4().hex
        values["is_active"] = True
        try:
            class_section_id = await ensure_class_section(
                session,
                name=payload.class_name,
                academic_year=payload.academic_year,
            )
            result = await session.execute(insert(table).values(**values).returning(table))
            await ensure_student_enrollment(
                session,
                student_id=values["id"],
                class_section_id=class_section_id,
                enrolled_at=date.today(),
            )
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A student with this admission number already exists",
            ) from error
        return StudentResponse(**dict(result.mappings().one()))

    @app.post(
        "/api/v1/students/import",
        response_model=StudentImportResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Students"],
    )
    async def import_students(
        request: Request,
        user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> StudentImportResponse:
        del user
        raw_csv = await request.body()
        if not raw_csv or len(raw_csv) > 2_000_000:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="CSV import must not exceed 2 MB",
            )
        try:
            decoded_csv = raw_csv.decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(decoded_csv))
            required_columns = {
                "admission_number",
                "first_name",
                "last_name",
                "date_of_birth",
                "gender",
                "class_name",
                "academic_year",
                "guardian_name",
                "guardian_phone",
            }
            if reader.fieldnames is None or not required_columns.issubset(reader.fieldnames):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "message": "CSV is missing required columns",
                        "required_columns": sorted(required_columns),
                    },
                )

            payloads: list[StudentAdmission] = []
            row_errors: list[dict[str, object]] = []
            for row_number, row in enumerate(reader, start=2):
                if len(payloads) + len(row_errors) >= 1000:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="CSV imports are limited to 1000 students",
                    )
                try:
                    payloads.append(StudentAdmission.model_validate(row))
                except ValidationError as error:
                    row_errors.append(
                        {
                            "row": row_number,
                            "errors": [
                                {
                                    "field": ".".join(str(part) for part in item["loc"]),
                                    "message": item["msg"],
                                }
                                for item in error.errors()
                            ],
                        }
                    )
            if not payloads and not row_errors:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="CSV contains no student rows",
                )
            if row_errors:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={"message": "CSV contains invalid student rows", "rows": row_errors},
                )
        except UnicodeDecodeError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="CSV must be UTF-8 encoded",
            ) from error
        except csv.Error as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="CSV could not be parsed",
            ) from error

        admission_numbers = [payload.admission_number for payload in payloads]
        if len(set(admission_numbers)) != len(admission_numbers):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="CSV contains duplicate admission numbers",
            )

        table = tenant_students_table(session)
        existing_result = await session.execute(
            select(table.c.admission_number).where(
                table.c.admission_number.in_(admission_numbers)
            )
        )
        if existing_result.scalars().first() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A student with an admission number in this CSV already exists",
            )

        values = []
        for payload in payloads:
            student_values = payload.model_dump()
            student_values["id"] = uuid.uuid4().hex
            student_values["is_active"] = True
            values.append(student_values)
        try:
            result = await session.execute(insert(table).returning(table), values)
            for student_values in values:
                class_section_id = await ensure_class_section(
                    session,
                    name=student_values["class_name"],
                    academic_year=student_values["academic_year"],
                )
                await ensure_student_enrollment(
                    session,
                    student_id=student_values["id"],
                    class_section_id=class_section_id,
                    enrolled_at=date.today(),
                )
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A student with an admission number in this CSV already exists",
            ) from error
        return StudentImportResponse(
            imported=len(values),
            students=[StudentResponse(**dict(row)) for row in result.mappings()],
        )

    @app.get(
        "/api/v1/students/{student_id}",
        response_model=StudentResponse,
        tags=["Students"],
    )
    async def get_student(
        student_id: str,
        user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> StudentResponse:
        del user
        table = tenant_students_table(session)
        result = await session.execute(select(table).where(table.c.id == student_id))
        row = result.mappings().one_or_none()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")
        return StudentResponse(**dict(row))

    @app.patch(
        "/api/v1/students/{student_id}",
        response_model=StudentResponse,
        tags=["Students"],
    )
    async def update_student(
        student_id: str,
        payload: StudentUpdate,
        user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> StudentResponse:
        del user
        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="At least one student field must be provided",
            )
        table = tenant_students_table(session)
        try:
            existing_result = await session.execute(
                select(table.c.class_name, table.c.academic_year).where(
                    table.c.id == student_id
                )
            )
            existing = existing_result.mappings().one_or_none()
            if existing is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Student not found",
                )
            result = await session.execute(
                update(table)
                .where(table.c.id == student_id)
                .values(**changes)
                .returning(table)
            )
            row = result.mappings().one_or_none()
            if row is None:
                await session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Student not found",
                )
            updated = dict(row)
            if (
                updated["class_name"] != existing["class_name"]
                or updated["academic_year"] != existing["academic_year"]
            ):
                class_section_id = await ensure_class_section(
                    session,
                    name=updated["class_name"],
                    academic_year=updated["academic_year"],
                )
                await ensure_student_enrollment(
                    session,
                    student_id=student_id,
                    class_section_id=class_section_id,
                    enrolled_at=date.today(),
                )
            await session.commit()
        except IntegrityError as error:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Student update conflicts with an existing record",
            ) from error
        return StudentResponse(**updated)

    @app.get(
        "/api/v1/students/{student_id}/enrollments",
        response_model=list[EnrollmentResponse],
        tags=["Classes and enrollments"],
    )
    async def list_student_enrollments(
        student_id: str,
        user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> list[EnrollmentResponse]:
        del user
        student_table, year_table, class_table, enrollment_table = tenant_academic_tables(session)
        student_result = await session.execute(
            select(student_table.c.id).where(student_table.c.id == student_id)
        )
        if student_result.scalar_one_or_none() is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")
        result = await session.execute(
            select(
                enrollment_table.c.id,
                enrollment_table.c.student_id,
                enrollment_table.c.class_section_id,
                class_table.c.name.label("class_name"),
                year_table.c.name.label("academic_year"),
                enrollment_table.c.enrolled_at,
                enrollment_table.c.status,
            )
            .select_from(
                enrollment_table.join(
                    class_table,
                    enrollment_table.c.class_section_id == class_table.c.id,
                ).join(
                    year_table,
                    class_table.c.academic_year_id == year_table.c.id,
                )
            )
            .where(enrollment_table.c.student_id == student_id)
            .order_by(year_table.c.name.desc(), enrollment_table.c.enrolled_at.desc())
        )
        return [EnrollmentResponse(**dict(row)) for row in result.mappings()]

    @app.post(
        "/api/v1/students/{student_id}/enrollments",
        response_model=EnrollmentResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Classes and enrollments"],
    )
    async def enroll_student(
        student_id: str,
        payload: EnrollmentCreate,
        user: Annotated[TenantUser, Depends(require_school_admin)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> EnrollmentResponse:
        del user
        student_table, year_table, class_table, enrollment_table = tenant_academic_tables(session)
        student_result = await session.execute(
            select(student_table.c.id).where(student_table.c.id == student_id)
        )
        if student_result.scalar_one_or_none() is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")

        class_result = await session.execute(
            select(
                class_table.c.id,
                class_table.c.name,
                year_table.c.name.label("academic_year"),
            )
            .select_from(
                class_table.join(
                    year_table,
                    class_table.c.academic_year_id == year_table.c.id,
                )
            )
            .where(
                class_table.c.id == payload.class_section_id,
                class_table.c.is_active.is_(True),
            )
        )
        class_row = class_result.mappings().one_or_none()
        if class_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class not found")

        await ensure_student_enrollment(
            session,
            student_id=student_id,
            class_section_id=payload.class_section_id,
            enrolled_at=payload.enrolled_at,
        )
        await session.execute(
            update(student_table)
            .where(student_table.c.id == student_id)
            .values(
                class_name=class_row["name"],
                academic_year=class_row["academic_year"],
            )
        )
        await session.commit()
        result = await session.execute(
            select(
                enrollment_table.c.id,
                enrollment_table.c.student_id,
                enrollment_table.c.class_section_id,
                class_table.c.name.label("class_name"),
                year_table.c.name.label("academic_year"),
                enrollment_table.c.enrolled_at,
                enrollment_table.c.status,
            )
            .select_from(
                enrollment_table.join(
                    class_table,
                    enrollment_table.c.class_section_id == class_table.c.id,
                ).join(
                    year_table,
                    class_table.c.academic_year_id == year_table.c.id,
                )
            )
            .where(
                enrollment_table.c.student_id == student_id,
                enrollment_table.c.class_section_id == payload.class_section_id,
                enrollment_table.c.status == "active",
            )
        )
        return EnrollmentResponse(**dict(result.mappings().one()))

    @app.get(
        "/api/v1/auth/me",
        response_model=SchoolUserResponse,
        tags=["Authentication"],
    )
    async def get_my_account(
        user: Annotated[TenantUser, Depends(require_school_user)],
    ) -> SchoolUserResponse:
        return SchoolUserResponse(id=user.id, email=user.email, role=user.role)

    @app.post(
        "/api/v1/auth/change-password",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["Authentication"],
    )
    async def change_password(
        payload: PasswordChange,
        user: Annotated[TenantUser, Depends(require_school_user)],
        session: Annotated[AsyncSession, Depends(tenant_db)],
    ) -> None:
        if not password_hasher.verify(payload.current_password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Current password is incorrect",
            )
        table = tenant_users_table(session)
        await session.execute(
            update(table)
            .where(table.c.id == user.id)
            .values(password_hash=password_hasher.hash(payload.new_password))
        )
        await session.commit()

    return app


app = create_app()
