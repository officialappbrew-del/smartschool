# School Management System — Complete Project Specification & Build Plan

**Version 1.0 | Prepared for AI Agent Build | For human reference and handoff**

---

## Introduction: What This Project Is

This document specifies the complete build plan for a **multi-tenant School Management System (SMS)** — a SaaS platform where multiple schools can each sign up, get their own branded subdomain (e.g., `greenwood.yoursms.com`), and manage their entire academic and administrative operations from one system.

**The problem it solves:** Schools today run on fragmented spreadsheets, WhatsApp groups, paper registers, and disconnected tools. Fee collection is manual, attendance is on paper, report cards take weeks to compile, and parents have no visibility into their child's progress. This platform replaces all of that with one connected system.

**Who uses it:**
- **School administrators** — manage students, staff, fees, and operations
- **Teachers** — mark attendance, enter grades, communicate with parents
- **Parents** — view their child's attendance, grades, report cards, and fee status
- **Students** — (later phases) view timetable, assignments, and results

**The core design decisions that make this project distinctive:**

1. **Multi-tenant SaaS from day one** — one deployment serves many schools, with strict data isolation between them. Each school's data lives in its own PostgreSQL schema, so a bug in one school's queries can never leak another school's data.

2. **Subdomain-based tenant resolution** — each school accesses the system at `schoolname.yoursms.com`. The subdomain itself identifies which school's data to load. No school ever has to select "which school am I?" — the URL tells the system.

3. **Offline-tolerant attendance and grade entry** — teachers in regions with unreliable internet can mark attendance and enter grades entirely offline. Records queue locally on the device and sync automatically when connectivity returns. This is the killer feature for schools in emerging markets.

4. **Web-first, mobile later** — the entire system works in a browser on any device (phone, tablet, laptop). Native mobile apps come in a later phase once the web product is proven.

**Technology stack:**
- **Backend:** FastAPI (Python) with `fastapi-tenancy` for multi-tenancy
- **Frontend:** Next.js 14+ (React) with App Router
- **Database:** PostgreSQL with schema-per-tenant isolation
- **Offline storage:** IndexedDB via Dexie.js
- **Hosting:** Vercel (frontend, wildcard SSL) + Railway/Fly.io (backend + database)

**What "done" looks like for v1:** A pilot school can enroll students, teachers can mark attendance offline, teachers can enter grades offline, the system generates report cards, parents log in to see their child's progress, and the school can invoice and collect fees online. Everything else is roadmap.

**How to read this document:**
- **Part 1–3** describe the architecture — read these to understand *how* the system works
- **Part 4** is the data model — read this to understand *what* the system stores
- **Part 5** is the offline sync design — the most technically complex piece
- **Part 6** is the phased feature roadmap — what gets built in what order
- **Part 7** is the AI agent build instructions — the actionable spec
- **Part 8** is the test requirements — how we know it works

Read the Introduction and Parts 1–2 first. Everything else is reference material the agent will need during the build.

---

## Part 1: System Architecture Overview

### Tech Stack (Non-Negotiable)

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Backend API | FastAPI (Python 3.11+) | All business logic, tenant resolution, auth |
| Multi-Tenancy | `fastapi-tenancy` with `resolution_strategy="subdomain"` and `isolation_strategy="schema"` | Subdomain → tenant mapping, PostgreSQL schema isolation |
| Database | PostgreSQL 15+ | One schema per school, shared database |
| Frontend | Next.js 14+ (App Router) | Server components for read views, client components for offline forms |
| Local Storage | IndexedDB via Dexie.js | Offline queue for attendance and grades |
| Hosting | Vercel (frontend + wildcard SSL) + Railway/Fly.io (FastAPI + PostgreSQL) | Wildcard `*.yoursms.com` with automatic SSL |
| Auth | JWT with `tenant_id` claim | Tenant context in every request |

### Request Flow Diagram

```
Browser (greenwood.yoursms.com)
    │
    ▼
Next.js Middleware
    ├── Extracts subdomain: "greenwood"
    ├── Strips client-supplied tenant headers
    ├── Validates tenant exists (cached)
    └── Forwards with x-tenant-subdomain: greenwood
    │
    ▼
FastAPI + fastapi-tenancy
    ├── Middleware resolves tenant from Host header subdomain
    ├── Sets schema context to "tenant_greenwood"
    └── All DB queries scoped to that schema automatically
```

**Critical security rule:** The client never dictates which tenant it belongs to. The subdomain in the URL determines it, and the Next.js middleware validates it before the request reaches FastAPI. This prevents any user from accessing another school's data by manipulating headers or tokens.

---

## Part 2: Multi-Tenancy Configuration (FastAPI)

### Environment Variables

```bash
TENANCY_DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/sms
TENANCY_RESOLUTION_STRATEGY=subdomain
TENANCY_DOMAIN_SUFFIX=.yoursms.com
TENANCY_ISOLATION_STRATEGY=schema
TENANCY_CACHE_ENABLED=true
TENANCY_JWT_SECRET=your-32-char-minimum-secret-here
```

### FastAPI Main Setup

```python
from fastapi import FastAPI
from fastapi_tenancy import TenancyConfig, TenancyManager, TenancyMiddleware
from fastapi_tenancy.storage.database import SQLAlchemyTenantStore

config = TenancyConfig()  # reads TENANCY_* env vars
store = SQLAlchemyTenantStore(config.database_url)
manager = TenancyManager(config, store)

app = FastAPI()
app.add_middleware(
    TenancyMiddleware,
    manager=manager,
    excluded_paths=["/health", "/api/v1/auth/login", "/api/v1/tenants/register"],
)
```

### Security Requirements

- `fastapi-tenancy` returns identical "Tenant not found" errors for missing, malformed, and unknown tenants — this prevents attackers from discovering which school names exist.
- JWT secret must be ≥32 characters. Algorithm `"none"` is rejected at construction.
- Never log the resolved schema name in error responses visible to clients.

### Tenant Registration Flow (Admin Only)

```python
# POST /api/v1/admin/tenants
# Headers: x-admin-key: <super-admin-secret>
# Body: {"identifier": "greenwood", "name": "Greenwood High School"}

tenant = await manager.register_tenant(
    identifier=request.identifier,
    name=request.name,
)
# This creates the PostgreSQL schema: tenant_greenwood
```

Only a super-admin (with a separate admin key, not a school user) can register new tenants. This prevents schools from self-registering without approval.

---

## Part 3: Next.js Middleware (Tenant Trust Boundary)

```typescript
// middleware.ts
import { NextRequest, NextResponse } from 'next/server';
import { validateTenant } from '@/lib/tenant-cache';

const RESERVED_SUBDOMAINS = ['www', 'app', 'admin', 'api'];

export async function middleware(request: NextRequest) {
  const hostname = request.headers.get('host') || '';
  const subdomain = hostname.split('.')[0];

  // Pass through reserved subdomains (marketing site, admin panel)
  if (RESERVED_SUBDOMAINS.includes(subdomain)) {
    return NextResponse.next();
  }

  // Strip any client-supplied tenant headers
  const requestHeaders = new Headers(request.headers);
  requestHeaders.delete('x-tenant-subdomain');
  requestHeaders.delete('x-tenant-id');

  // Validate tenant exists (cache this aggressively)
  const tenant = await validateTenant(subdomain);
  if (!tenant) {
    return NextResponse.redirect(new URL('/tenant-not-found', request.url));
  }

  // Forward validated tenant context to FastAPI
  requestHeaders.set('x-tenant-subdomain', subdomain);

  return NextResponse.next({
    request: { headers: requestHeaders },
  });
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico|api).*)'],
};
```

### DNS & SSL Setup (Vercel)

1. Point domain nameservers to `ns1.vercel-dns.com`, `ns2.vercel-dns.com`
2. Add `*.yoursms.com` as a wildcard domain in the Vercel project
3. Vercel automatically issues one wildcard SSL certificate covering all first-level subdomains — no per-school certificate management needed

---

## Part 4: Core Data Model (Per-Tenant Schema)

Every table below exists **inside each school's schema** (`tenant_{identifier}`). When a teacher at Greenwood marks attendance, the query runs against `tenant_greenwood.attendance_records`, not a shared table. This is what makes tenant isolation bulletproof — even a SQL injection or logic bug cannot cross schools, because the schema boundary is enforced at the connection level.

### Entity Relationship Summary

```
AcademicYear ──┬── Term
               └── Enrollment ──── Student ──── Guardian
                        │
                        └── ClassSection ──── TeacherAssignment ──── Subject
                                                    │
                                                    └── Assessment ──── Grade ──── ReportCard

FeeStructure ──── Invoice ──── Payment
Attendance ──── Student (per session, per date)
```

### Core Tables (SQLAlchemy Models)

```python
# models/core.py — All models inherit TenantBase

class AcademicYear(TenantBase):
    __tablename__ = "academic_years"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str]  # "2025-2026"
    start_date: Mapped[date]
    end_date: Mapped[date]
    is_current: Mapped[bool] = mapped_column(default=False)

class Term(TenantBase):
    __tablename__ = "terms"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    academic_year_id: Mapped[UUID] = mapped_column(ForeignKey("academic_years.id"))
    name: Mapped[str]  # "Term 1", "Term 2"
    start_date: Mapped[date]
    end_date: Mapped[date]

class Student(TenantBase):
    __tablename__ = "students"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    admission_number: Mapped[str] = mapped_column(unique=True)
    first_name: Mapped[str]
    last_name: Mapped[str]
    date_of_birth: Mapped[date]
    gender: Mapped[str]
    medical_notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True)

class Guardian(TenantBase):
    __tablename__ = "guardians"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    first_name: Mapped[str]
    last_name: Mapped[str]
    email: Mapped[str] = mapped_column(unique=True)
    phone: Mapped[str]
    user_id: Mapped[UUID | None]  # links to auth user for parent portal

class StudentGuardian(TenantBase):
    __tablename__ = "student_guardians"
    student_id: Mapped[UUID] = mapped_column(ForeignKey("students.id"), primary_key=True)
    guardian_id: Mapped[UUID] = mapped_column(ForeignKey("guardians.id"), primary_key=True)
    relationship: Mapped[str]  # "Mother", "Father", "Guardian"

class ClassSection(TenantBase):
    __tablename__ = "class_sections"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str]  # "Grade 5A"
    academic_year_id: Mapped[UUID] = mapped_column(ForeignKey("academic_years.id"))

class Subject(TenantBase):
    __tablename__ = "subjects"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]

class Enrollment(TenantBase):
    __tablename__ = "enrollments"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    student_id: Mapped[UUID] = mapped_column(ForeignKey("students.id"))
    class_section_id: Mapped[UUID] = mapped_column(ForeignKey("class_sections.id"))
    academic_year_id: Mapped[UUID] = mapped_column(ForeignKey("academic_years.id"))
    enrolled_at: Mapped[date]
    status: Mapped[str] = mapped_column(default="active")  # active | transferred | graduated

class TeacherAssignment(TenantBase):
    __tablename__ = "teacher_assignments"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    staff_user_id: Mapped[UUID]  # links to auth user with role=teacher
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.id"))
    class_section_id: Mapped[UUID] = mapped_column(ForeignKey("class_sections.id"))
    academic_year_id: Mapped[UUID] = mapped_column(ForeignKey("academic_years.id"))
```

### Attendance & Grades (Offline-Sync Tables)

These tables are special because they're written from offline clients. The extra sync metadata fields (`local_id`, `client_timestamp`) enable idempotency and conflict resolution.

```python
# models/offline_sync.py

class AttendanceRecord(TenantBase):
    __tablename__ = "attendance_records"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    enrollment_id: Mapped[UUID] = mapped_column(ForeignKey("enrollments.id"))
    date: Mapped[date]
    status: Mapped[str]  # present | absent | late | excused
    marked_by_user_id: Mapped[UUID]
    marked_at: Mapped[datetime]
    # Sync metadata for conflict detection and idempotency
    local_id: Mapped[str | None] = mapped_column(unique=True)  # client-generated UUID
    client_timestamp: Mapped[datetime]  # when teacher pressed "save"

class Assessment(TenantBase):
    __tablename__ = "assessments"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    teacher_assignment_id: Mapped[UUID] = mapped_column(ForeignKey("teacher_assignments.id"))
    name: Mapped[str]  # "Mid-Term Quiz"
    max_score: Mapped[Decimal]
    term_id: Mapped[UUID] = mapped_column(ForeignKey("terms.id"))

class Grade(TenantBase):
    __tablename__ = "grades"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    assessment_id: Mapped[UUID] = mapped_column(ForeignKey("assessments.id"))
    enrollment_id: Mapped[UUID] = mapped_column(ForeignKey("enrollments.id"))
    score: Mapped[Decimal]
    graded_by_user_id: Mapped[UUID]
    graded_at: Mapped[datetime]
    local_id: Mapped[str | None] = mapped_column(unique=True)
    client_timestamp: Mapped[datetime]
```

### Fee Tables (Immutable Financial Records)

Financial records are never deleted — they're voided with a timestamp. This preserves the audit trail, which schools need for regulatory compliance and dispute resolution.

```python
class FeeStructure(TenantBase):
    __tablename__ = "fee_structures"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    class_section_id: Mapped[UUID] = mapped_column(ForeignKey("class_sections.id"))
    term_id: Mapped[UUID] = mapped_column(ForeignKey("terms.id"))
    amount: Mapped[Decimal]
    description: Mapped[str]

class Invoice(TenantBase):
    __tablename__ = "invoices"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    enrollment_id: Mapped[UUID] = mapped_column(ForeignKey("enrollments.id"))
    term_id: Mapped[UUID] = mapped_column(ForeignKey("terms.id"))
    amount: Mapped[Decimal]
    status: Mapped[str]  # issued | paid | overdue
    issued_at: Mapped[date]
    due_date: Mapped[date]

class Payment(TenantBase):
    __tablename__ = "payments"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    invoice_id: Mapped[UUID] = mapped_column(ForeignKey("invoices.id"))
    amount: Mapped[Decimal]
    gateway_reference: Mapped[str] = mapped_column(unique=True)
    paid_at: Mapped[datetime]
    # NEVER hard-delete payments. Use voided_at for reversals.
    voided_at: Mapped[datetime | None]
```

**Agent rule:** No `DELETE` on `Invoices` or `Payments`. Use `voided_at` field and filter queries by `voided_at IS NULL`.

---

## Part 5: Offline Sync Architecture (Attendance & Grades)

This is the most technically complex piece of the system, and the one that makes it valuable for schools with unreliable internet. The principle is **local-first**: the UI updates immediately, the network is an enhancement, and no record is ever lost because the connection dropped.

### IndexedDB Schema (Dexie.js)

```typescript
// lib/db.ts
import Dexie, { Table } from 'dexie';

type SyncStatus = 'pending' | 'syncing' | 'synced' | 'failed';

interface PendingMutation {
  id?: number;
  localId: string;           // UUID generated on client
  tenantSubdomain: string;   // "greenwood" — CRITICAL for security
  endpoint: string;          // "/api/v1/attendance/batch"
  method: 'POST' | 'PUT';
  body: string;              // JSON string
  attemptCount: number;
  createdAt: number;
  lastError?: string;
}

class SchoolDB extends Dexie {
  pendingMutations!: Table<PendingMutation, number>;

  constructor() {
    super('school-offline-db');
    this.version(1).stores({
      pendingMutations: '++id, localId, tenantSubdomain, createdAt'
    });
  }
}

export const db = new SchoolDB();
```

### Write Path (Client)

```typescript
// lib/offline.ts
import { db } from './db';
import { v4 as uuidv4 } from 'uuid';

export async function saveAttendanceOffline(
  tenantSubdomain: string,
  records: Array<{ enrollmentId: string; status: string; date: string }>
) {
  const localId = uuidv4();
  const mutation = {
    localId,
    tenantSubdomain,
    endpoint: '/api/v1/attendance/batch',
    method: 'POST' as const,
    body: JSON.stringify({ records, localId }),
    attemptCount: 0,
    createdAt: Date.now(),
  };

  // 1. Write to IndexedDB FIRST — UI responds immediately
  await db.pendingMutations.add(mutation);

  // 2. Attempt immediate sync if online
  if (navigator.onLine) {
    drainQueue(tenantSubdomain);
  }

  return localId;
}
```

### Queue Drain (Client)

```typescript
// lib/offline.ts (continued)

let isDraining = false;

export async function drainQueue(tenantSubdomain: string) {
  if (isDraining || !navigator.onLine) return;
  isDraining = true;

  try {
    const pending = await db.pendingMutations
      .where('tenantSubdomain').equals(tenantSubdomain)
      .sortBy('createdAt');

    for (const mutation of pending) {
      await db.pendingMutations.update(mutation.id!, { attemptCount: mutation.attemptCount + 1 });

      try {
        const response = await fetch(mutation.endpoint, {
          method: mutation.method,
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${getToken()}`,
            // NOTE: No tenant header here. The subdomain in the URL determines tenant.
          },
          body: mutation.body,
        });

        if (response.ok || response.status === 409) {
          // 409 = duplicate (already synced), treat as success
          await db.pendingMutations.delete(mutation.id!);
        } else if (response.status >= 500) {
          throw new Error(`Server error: ${response.status}`);
        } else {
          // 4xx client error — permanent failure, notify user
          await db.pendingMutations.update(mutation.id!, {
            lastError: `Permanent failure: ${response.status}`
          });
        }
      } catch (error) {
        // Network error — retry with exponential backoff (handled by drain interval)
        await db.pendingMutations.update(mutation.id!, {
          lastError: String(error)
        });
        break; // Stop draining, try next interval
      }
    }
  } finally {
    isDraining = false;
  }
}

// Drain on reconnect + periodic retry
window.addEventListener('online', () => drainQueue(getTenantSubdomain()));
setInterval(() => drainQueue(getTenantSubdomain()), 30_000);
```

### Server Endpoint (FastAPI)

```python
# api/attendance.py
from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi_tenancy.dependencies import get_current_tenant, make_tenant_db_dependency

router = APIRouter()
get_db = make_tenant_db_dependency(manager)

@router.post("/attendance/batch")
async def sync_attendance_batch(
    payload: AttendanceBatchPayload,
    db: AsyncSession = Depends(get_db),
    tenant = Depends(get_current_tenant),
    authorization: str = Header(...),
):
    # Tenant is already resolved from subdomain by middleware
    # Validate JWT and ensure tenant claim matches resolved tenant
    token_tenant = decode_jwt_tenant(authorization)
    if token_tenant != tenant.identifier:
        raise HTTPException(403, "Tenant mismatch")

    # Idempotency: check if local_id already exists
    existing = await db.execute(
        select(AttendanceRecord).where(
            AttendanceRecord.local_id == payload.localId
        )
    )
    if existing.scalar_one_or_none():
        return {"status": "duplicate", "localId": payload.localId}

    # Insert all records atomically
    for record in payload.records:
        db.add(AttendanceRecord(
            enrollment_id=record.enrollmentId,
            date=record.date,
            status=record.status,
            marked_by_user_id=current_user.id,
            marked_at=datetime.utcnow(),
            local_id=payload.localId,
            client_timestamp=record.clientTimestamp,
        ))
    await db.commit()
    return {"status": "synced", "localId": payload.localId}
```

**Why `local_id` matters:** The unique constraint in PostgreSQL prevents duplicate attendance records even if the client retries the same mutation many times. Without it, a flaky connection could create 3 copies of the same attendance record — a data integrity nightmare the school would never trust again.

---

## Part 6: Full Feature Roadmap (Phased)

### Phase 0: Foundation (Weeks 1–2)

The goal of Phase 0 is to prove that multi-tenancy and tenant isolation actually work. No school features are built until this is verified.

| Feature | Acceptance Criteria |
|---------|---------------------|
| Wildcard DNS + SSL | `greenwood.yoursms.com` and `oakwood.yoursms.com` both serve the app over HTTPS |
| Tenant registration (admin API) | `POST /api/v1/admin/tenants` creates schema `tenant_{id}` |
| Tenant resolution | FastAPI resolves tenant from subdomain; all DB queries scoped |
| Tenant isolation proof | Tenant A cannot read Tenant B's data via any API endpoint |
| Next.js middleware | Strips client tenant headers, validates subdomain, redirects unknown |
| Auth skeleton | Login returns JWT with `tenant_id` claim |

**Do not proceed until all Phase 0 criteria pass.**

### Phase 1: MVP — Attendance + Grades + Parent View (Weeks 3–8)

| Feature | Priority | Details |
|---------|----------|---------|
| Student admission | P0 | CRUD students, guardians, enrollment. CSV import (students, guardians, class assignments). |
| Attendance marking (offline) | P0 | Teacher selects class → marks present/absent/late → IndexedDB first → syncs when online. |
| Grade entry (offline) | P0 | Teacher selects assessment → enters scores → IndexedDB first → syncs. |
| Report card generation | P0 | Server-side computation from synced grades. PDF export. |
| Parent portal (read-only) | P0 | Guardian logs in → sees child's attendance history, grades, report cards. |
| Teacher dashboard | P1 | Today's classes, pending sync count, quick links to mark attendance. |
| Basic notifications | P1 | Email on report card published, attendance threshold alerts. |

**Pilot school deployment after Week 6** — even if only attendance works. Real teacher feedback on the offline UX is worth more than another month of building.

### Phase 2: Fees + Communication (Weeks 9–14)

| Feature | Priority | Details |
|---------|----------|---------|
| Fee structure setup | P0 | Per class/section per term. Line items (tuition, transport, etc.). |
| Invoice generation | P0 | Bulk generate for all enrolled students per term. |
| Payment recording | P0 | Manual entry (cash/bank transfer) + gateway integration (Stripe/Paystack). |
| Fee dashboard | P1 | Collection rate, outstanding balances, defaulters list. |
| Parent fee view | P0 | Invoice list, payment history, pay online button. |
| Announcements | P1 | Admin publishes → parents see in portal + email digest. |
| Teacher-parent messaging | P2 | Threaded messages, moderation flag. |

### Phase 3: Operations & Analytics (Weeks 15–20)

| Feature | Priority | Details |
|---------|----------|---------|
| Timetable management | P1 | Class → subject → teacher → room → period. Conflict detection. |
| Staff records | P1 | Profiles, qualifications, contracts. |
| Library management | P2 | Catalog, issue/return, fines. |
| Transport | P2 | Routes, bus assignment, pickup logs. |
| Analytics dashboard | P1 | Enrollment trends, attendance rates, fee collection, grade distributions. |
| Custom reports | P2 | Report builder (filter by class, term, date range). |

### Phase 4: Advanced (Post-Launch)

- Payroll integration
- Hostel/boarding management
- LMS integration (Google Classroom, Teams)
- Mobile apps (React Native or Flutter)
- Multi-campus support (sub-schools within a tenant)
- Biometric/RFID attendance hardware integration

---

## Part 7: Build Instructions for the AI Agent

### Setup Commands

```bash
# Backend
mkdir backend && cd backend
python -m venv venv && source venv/bin/activate
pip install fastapi uvicorn "fastapi-tenancy[postgres,jwt,migrations]" sqlalchemy asyncpg alembic
pip install python-jose[cryptography] passlib[bcrypt] python-multipart

# Frontend
npx create-next-app@latest frontend --typescript --tailwind --app --src-dir
cd frontend
npm install dexie dexie-react-hooks uuid
```

### Project Structure

```
backend/
├── app/
│   ├── main.py                 # FastAPI app + tenancy middleware
│   ├── config.py               # TenancyConfig, JWT settings
│   ├── dependencies.py         # get_db, get_current_user, require_role
│   ├── models/
│   │   ├── core.py             # AcademicYear, Student, Guardian, Enrollment...
│   │   ├── attendance.py
│   │   ├── grades.py
│   │   └── fees.py
│   ├── schemas/                # Pydantic request/response models
│   ├── api/
│   │   ├── admin/tenants.py    # Register/suspend tenants
│   │   ├── auth.py             # Login, token refresh
│   │   ├── students.py
│   │   ├── attendance.py       # Batch sync endpoint
│   │   ├── grades.py           # Batch sync endpoint
│   │   ├── fees.py
│   │   └── reports.py          # Report card generation
│   └── services/
│       ├── report_card.py      # Server-side grade computation
│       └── notifications.py
├── alembic/                    # Per-tenant migrations
│   └── versions/
└── tests/

frontend/
├── src/
│   ├── middleware.ts           # Tenant resolution
│   ├── lib/
│   │   ├── db.ts               # Dexie schema
│   │   ├── offline.ts          # Queue + drain logic
│   │   └── api.ts              # Fetch wrapper with auth
│   ├── app/
│   │   ├── (auth)/login/
│   │   ├── (dashboard)/
│   │   │   ├── attendance/     # Teacher offline marking
│   │   │   ├── grades/         # Teacher offline entry
│   │   │   ├── students/
│   │   │   └── fees/
│   │   └── (parent)/
│   │       └── portal/         # Parent read-only views
│   └── components/
│       ├── SyncStatusBadge.tsx # Shows pending/synced count
│       └── OfflineForm.tsx     # Reusable offline wrapper
```

### Agent Constraints (Must Follow)

These are non-negotiable rules the agent must obey throughout the build. Violating any of these creates data integrity or security problems that are expensive to fix later.

1. **Tenant isolation:** Never write a query that doesn't go through the tenancy middleware. Every `SELECT` is schema-scoped automatically — do not add manual `tenant_id` filters (schema isolation handles it).

2. **No hard deletes on financial data:** `Invoices` and `Payments` use `voided_at` timestamps. Student records use `is_active` flags.

3. **Offline writes first:** Every attendance/grade mutation must write to IndexedDB **before** attempting network. The UI should not wait for a server response.

4. **local_id uniqueness:** Every batch sync endpoint must check `local_id` before insert. Duplicate → return 200 with `status: "duplicate"`, not an error.

5. **Report cards computed server-side only:** Never trust client-side grade computation. The server recomputes from synced `Grade` records.

6. **JWT tenant claim validation:** In every authenticated endpoint, verify `token.tenant_id == resolved_tenant.identifier`. Mismatch → 403.

7. **Pilot school data import:** Build a CSV import endpoint for students and guardians before any pilot school onboarding. Schools have Excel data, and without import you cannot onboard them.

---

## Part 8: Test Requirements Before Pilot

Before any school is given access, all of these tests must pass. These are the criteria that determine whether the system is trustworthy enough for real student data.

| Test | Pass Criteria |
|------|---------------|
| Tenant isolation | Create two tenants, insert data in each, verify Tenant A API returns zero Tenant B records |
| Offline attendance | Mark attendance in airplane mode, close browser, reopen online, verify record syncs exactly once |
| Duplicate sync | Send same `local_id` twice, verify second returns duplicate status and no second record created |
| Report card | Enter grades for 10 students, generate report cards, verify all scores computed correctly |
| Parent access | Parent of Student A cannot see Student B's data (403) |
| Subdomain validation | `invalid.yoursms.com` returns tenant-not-found, does not leak valid tenant names |

---
