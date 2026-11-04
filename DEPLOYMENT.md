# Deployment Guide

## Overview

This guide explains how to deploy the School Management System (SMS) with:
- **Frontend (Next.js)** on **Vercel**
- **Backend (FastAPI)** on **Render**
- **Both** in the **same GitHub repository**

The repository has a monorepo layout:

```
SMS/
+-- backend/            # FastAPI + PostgreSQL (Render)
+-- frontend/           # Next.js (Vercel)
+-- README.md
+-- description.md
+-- DEPLOYMENT.md
## Prerequisites

- A **GitHub** account with this repository pushed
- A **Vercel** account (free tier works)
- A **Render** account (free tier works)
- A **PostgreSQL** database (Render provides one, or use Neon/Supabase/AWS RDS)
- A domain name (e.g. `yoursms.com`) for wildcard subdomains
- An SMTP provider (Gmail, SendGrid, Mailgun) for email invitations
## Architecture

| Component | Host | Purpose |
|-----------|------|---------|
| Frontend | `vercel.com` | Next.js app served at `*.yoursms.com` |
| Backend API | `render.com` | FastAPI at `api.yoursms.com` |
| Database | `render.com` (PostgreSQL) | One schema per tenant |
| DNS | Your domain registrar | Wildcard `*.yoursms.com` -> Vercel |
| Email | SMTP provider | Invitations, notifications |
## Step 1: Prepare the Repository

### 1.1 Create a `.gitignore` at repo root (already present, verify)

```gitignore
# Node.js
frontend/node_modules/
frontend/.next/
frontend/out/
frontend/tsconfig.tsbuildinfo

# Python
backend/.venv/
backend/__pycache__/
backend/**/*.py[cod]

# Local configuration
.env
.env.*
!.env.example
!backend/.env.example
```

### 1.2 Add root-level files

Create `package.json` at the repo root for tooling:

```json
{
  "name": "school-management",
  "private": true,
  "workspaces": ["frontend"],
  "scripts": {
    "dev": "cd frontend && npm run dev",
    "build": "cd frontend && npm run build"
  }
}
```

### 1.3 Push to GitHub

```bash
git init
git add .
git commit -m "first commit"
git branch -M main
git remote add origin https://github.com/EkeHanson/smartSchool.git
git push -u origin main
```
## Step 2: Backend on Render

### 2.1 Create a Render Web Service

1. Sign in at `https://dashboard.render.com`
2. Click **New** -> **Web Service**
3. Connect your GitHub repo (`YOUR_USERNAME/sms`)
4. Configure:

| Field | Value |
|-------|-------|
| **Name** | `sms-backend` |
| **Environment** | `Python 3` |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| **Root Directory** | `backend` |
| **Instance Type** | `Free` (or `Starter` for always-on) |

### 2.2 Add Environment Variables

In the Render dashboard, under **Settings > Environment**, add:

```env
# Database
TENANCY_DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/DB_NAME

# Domain
TENANCY_DOMAIN_SUFFIX=.yoursms.com
TENANCY_RESOLUTION_STRATEGY=subdomain
TENANCY_ISOLATION_STRATEGY=schema

# JWT
TENANCY_JWT_SECRET=your-32-char-minimum-secret-here

# Frontend
FRONTEND_ORIGIN=https://yourapp.com
PUBLIC_APP_BASE_URL=https://yourapp.com

# Email (SMTP)
EMAIL_DELIVERY_MODE=sync
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=true
EMAIL_HOST_USER=your-email@gmail.com
EMAIL_HOST_PASSWORD=your-app-password
DEFAULT_FROM_EMAIL=your-email@gmail.com
SERVER_EMAIL=your-email@gmail.com

# Billing credentials (required to store tenant gateway secrets)
BILLING_ENCRYPTION_KEY=generate-a-Fernet-key-and-store-it-securely
```

Generate `BILLING_ENCRYPTION_KEY` with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and keep it stable. Changing this key makes saved payment-provider credentials unreadable.

### 2.3 Set Up PostgreSQL

**Option A: Render PostgreSQL (easiest)**
1. In Render dashboard, click **New** -> **PostgreSQL**
2. Name it `sms-db`
3. Copy the `DATABASE_URL` (e.g. `postgresql://user:pass@host/db`)
4. Set it as `TENANCY_DATABASE_URL` in the web service env

**Option B: External PostgreSQL (Neon, Supabase, AWS RDS)**
1. Create your external database
2. Copy the connection URL
3. Set it as `TENANCY_DATABASE_URL`

### 2.4 Verify Backend

After deployment, check:
- `https://sms-backend-cct4.onrender.com/health` returns `{"status": "ok"}`
- `https://sms-backend-cct4.onrender.com/docs` shows the API docs
## Step 3: Frontend on Vercel

### 3.1 Import Project

1. Sign in at `https://vercel.com`
2. Click **Add New** -> **Import Git Repository**
3. Select `YOUR_USERNAME/sms`
4. Configure:

| Field | Value |
|-------|-------|
| **Project Name** | `sms-frontend` |
| **Root Directory** | `frontend` |
| **Framework Preset** | `Next.js` |

### 3.2 Environment Variables

Under **Settings > Environment**, add:

```env
NEXT_PUBLIC_API_BASE_URL=https://sms-backend-cct4.onrender.com
```

### 3.3 Configure `next.config.mjs` (if needed)

The current `next.config.mjs` is empty. If you need custom config (e.g. rewrites for API proxying), update it:

```javascript
/** @type {import('next').NextConfig} */
const nextConfig = {
  rewrites: async () => [
    {
      source: '/api/:path*',
      destination: '${NEXT_PUBLIC_API_BASE_URL}/api/:path*',
    },
  ],
};
export default nextConfig;
```

### 3.4 Add Custom Domain

1. In Vercel project, go to **Settings > Domains**
2. Add your apex domain: `yoursms.com`
3. Add wildcard domain: `*.yoursms.com`
4. Vercel will provide DNS records to add to your registrar

### 3.5 Verify Frontend

After deployment, `https://sms-frontend.vercel.app` should serve the app.
## Step 4: DNS & SSL (Wildcard)

### 4.1 Point Nameservers

Point your domain `yoursms.com` to Vercel nameservers:
- `ns1.vercel-dns.com`
- `ns2.vercel-dns.com`

### 4.2 Add Wildcard Domain in Vercel

1. In Vercel project, go to **Settings > Domains**
2. Add `*.yoursms.com` as a wildcard domain
3. Vercel automatically issues one wildcard SSL certificate covering all first-level subdomains

### 3.3 DNS Records (if not using Vercel nameservers)

Add these records at your domain registrar:

| Type | Name | Value |
|------|------|-------|
| A | `@` | `75.2.74.150` (Vercel edge) |
| CNAME | `*` | `cname.vercel-dns.com` |

### 4.4 Verify SSL

- `https://greenwood.yoursms.com` should load over HTTPS
- `https://oakwood.yoursms.com` should load over HTTPS
- Both show a valid SSL certificate (lock icon)
## Step 5: Tenant Resolution Flow

### 5.1 How Subdomains Work

```
Browser: greenwood.yoursms.com
    �
    ?
Vercel Edge (Next.js Middleware)
    +-- Extracts subdomain: "greenwood"
    +-- Strips client-supplied tenant headers
    +-- Validates tenant exists (cached)
    +-- Forwards with x-tenant-subdomain: greenwood
    �
    ?
Render (FastAPI + fastapi-tenancy)
    +-- Middleware resolves tenant from Host header subdomain
    +-- Sets schema context to "tenant_greenwood"
    +-- All DB queries scoped to that schema automatically
```

### 5.2 CORS Configuration

The backend `app/main.py` already configures CORS to accept:
- The configured `FRONTEND_ORIGIN`
- Any subdomain of `TENANCY_DOMAIN_SUFFIX`

For production, set:
```env
FRONTEND_ORIGIN=https://yoursms.com
TENANCY_DOMAIN_SUFFIX=.yoursms.com
```

### 5.3 Reserved Subdomains

These subdomains are reserved and bypass tenant resolution:
- `www` - Marketing site
- `app` - Generic app entry
- `admin` - Super-admin dashboard
- `api` - API documentation
## Step 6: Initial Setup (Post-Deployment)

### 6.1 Create Platform Administrator

```bash
cd backend
python manage_platform_admin.py create
```

Enter the operator email and a strong password. The command creates the `platform_admins` table in the configured PostgreSQL database.

### 6.2 Access Super-Admin Dashboard

Open `https://admin.yoursms.com/` and sign in with the platform administrator email and password.

### 6.3 Create Your First Tenant

1. Sign in to the super-admin dashboard
2. Click **Create a school tenant**
3. Enter:
   - **School name**: e.g. "Greenwood High School"
   - **URL slug**: e.g. "greenwood"
4. The dashboard creates the tenant and runs its database migrations

### 6.4 Invite School Administrator

1. In the tenant list, enter the first school administrator's email
2. Click **Invite admin**
3. The invitation link expires after 48 hours

### 6.5 Access School Workspace

The school administrator follows the emailed link to set a password, then signs in at:
- `https://greenwood.yoursms.com/login`

The school workspace is:
- `https://greenwood.yoursms.com/`

### 6.6 Configure Tenant Billing

1. Keep `BILLING_ENCRYPTION_KEY` set to the same secret on every backend instance. Generate it once with:

   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

2. Sign in as a school administrator and open **Fees** to set the tenant's currency and choose manual, Paystack, or Stripe payments.
3. Configure fee items for each class and term, generate invoices, and record manual payments. The fee dashboard shows collection rates, outstanding balances, and overdue accounts.
4. For online checkout, configure the provider webhook for each tenant's routed API host at `/api/v1/billing/webhooks/paystack` or `/api/v1/billing/webhooks/stripe`. Stripe must send `checkout.session.completed` and `checkout.session.async_payment_succeeded`; save the Stripe endpoint signing secret in the tenant's **Fees** settings. Paystack signatures use its API secret key.

Existing tenants must be migrated to the current Alembic revision using **Run migrations** in the platform super-admin dashboard before using billing, announcements, messaging, timetable management, or staff records. Newly created tenants receive the current migration automatically.

### 6.7 School Announcements

School administrators can save announcement drafts and publish them from the school workspace. Published announcements appear in the parent portal and are emailed to each distinct guardian email on an active student record. Configure the existing `EMAIL_*` SMTP settings before publishing announcements with recipients. Per-recipient delivery results are stored; failed and pending deliveries can be retried from the workspace.

### 6.8 Teacher-Parent Messaging

Parents can initiate private message threads with teachers assigned to their child's active class. Teachers reply from their workspace. Either participant can flag a received message for review; school administrators review and dismiss or resolve open reports from **Message moderation**. Thread access is rechecked against the current guardian-student link and teacher-class assignment on every request.

### 6.9 Timetable Management

School administrators can schedule weekly lessons by active class, assigned teacher, subject, weekday, time, and optional room. Overlapping class, teacher, and room bookings are rejected. Teachers see their assigned schedule under **My timetable**. Existing tenants must run the latest Alembic migrations from the platform super-admin dashboard before timetable setup.

### 6.10 Staff Records

School administrators can maintain staff profiles, qualifications, and contract records from **Staff records** in the school workspace. Profiles may optionally link to an existing teacher account in the same tenant; non-teaching staff do not need login accounts. Staff numbers and linked teacher accounts are unique per tenant. Contract date ranges are validated, and contracts can be marked draft, active, or ended. Run the latest Alembic migration from the platform super-admin dashboard for existing tenants; new tenants receive the current schema automatically.

### 6.11 School Analytics

The school overview and **Analytics** page report active students and class enrollments, month-by-month enrollment and attendance trends over a rolling 12-month period, fee collection by currency, and the distribution and average of saved grades. Attendance rates treat late marks as attended and exclude excused marks. Fee totals omit voided invoices/payments. This feature queries existing tenant tables and requires no additional migration.

## Step 7: Environment Variables Reference

### Backend (Render)

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `TENANCY_DATABASE_URL` | Yes | PostgreSQL connection URL | `postgresql://user:pass@host:5432/db` |
| `TENANCY_DOMAIN_SUFFIX` | Yes | Domain suffix for subdomains | `.yoursms.com` |
| `TENANCY_JWT_SECRET` | Yes | JWT signing secret (>=32 chars) | `your-random-secret-here` |
| `TENANCY_CACHE_ENABLED` | No | Cache tenant lookups | `true` or `false` |
| `FRONTEND_ORIGIN` | Yes | Frontend URL for CORS | `https://yoursms.com` |
| `PUBLIC_APP_BASE_URL` | Yes | Public app base URL | `https://yoursms.com` |
| `BILLING_ENCRYPTION_KEY` | Gateway payments | Stable Fernet key used to encrypt tenant gateway secrets | Generated Fernet key |
| `EMAIL_HOST` | Yes* | SMTP host | `smtp.gmail.com` |
| `EMAIL_PORT` | Yes* | SMTP port | `587` |
| `EMAIL_USE_TLS` | Yes* | Use STARTTLS | `true` |
| `EMAIL_HOST_USER` | Yes* | SMTP username | `your-email@gmail.com` |
| `EMAIL_HOST_PASSWORD` | Yes* | SMTP password | `your-app-password` |
| `DEFAULT_FROM_EMAIL` | Yes* | From email address | `noreply@yoursms.com` |

*Required for email invitations and notifications.

### Frontend (Vercel)

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `NEXT_PUBLIC_API_BASE_URL` | Yes | Backend API URL | `https://sms-backend.onrender.com` |
## Step 8: Domain Setup (Detailed)

### 8.1 Buy a Domain

Purchase a domain like `yoursms.com` from any registrar (Namecheap, GoDaddy, Google Domains).

### 8.2 Point Nameservers to Vercel

In your domain registrar:
1. Find **Nameservers** or **DNS** settings
2. Replace with Vercel nameservers:
   - `ns1.vercel-dns.com`
   - `ns2.vercel-dns.com`

### 8.3 Add Wildcard Domain in Vercel

1. Open your Vercel project
2. Go to **Settings > Domains**
3. Add `*.yoursms.com`
4. Vercel will show a wildcard SSL certificate is active

### 8.4 Add Apex Domain in Vercel

1. In **Settings > Domains**, add `yoursms.com`
2. Vercel will provide an IP address or CNAME
3. Add this as an `A` record at your registrar if needed

### 8.5 Verify DNS Propagation

```bash
dig greenwood.yoursms.com
dig oakwood.yoursms.com
```

Both should resolve to Vercel edge IPs.
## Step 9: Backend on Render (Detailed)

### 9.1 Create a Render YAML Spec

Create `render.yaml` at the repo root:

```yaml
databases:
  - name: sms-db
    databaseName: sms
    plan: free

services:
  - type: web
    name: sms-backend
    env: python
    region: oregon
    plan: free
    rootDirectory: backend
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT
    envVars:
      - key: TENANCY_DATABASE_URL
        fromDatabase:
          databaseName: sms
      - key: TENANCY_DOMAIN_SUFFIX
        value: .yoursms.com
      - key: TENANCY_JWT_SECRET
        value: your-32-char-minimum-secret-here
      - key: BILLING_ENCRYPTION_KEY
        sync: false
      - key: FRONTEND_ORIGIN
        value: https://yoursms.com
      - key: PUBLIC_APP_BASE_URL
        value: https://yoursms.com
      - key: EMAIL_DELIVERY_MODE
        value: sync
      - key: EMAIL_HOST
        value: smtp.gmail.com
      - key: EMAIL_PORT
        value: "587"
      - key: EMAIL_USE_TLS
        value: "true"
      - key: EMAIL_HOST_USER
        value: your-email@gmail.com
      - key: EMAIL_HOST_PASSWORD
        value: your-app-password
      - key: DEFAULT_FROM_EMAIL
        value: your-email@gmail.com
      - key: SERVER_EMAIL
        value: your-email@gmail.com
```

### 9.2 Deploy via Render Dashboard

1. Go to `https://dashboard.render.com`
2. Click **New** -> **Blueprint**
3. Connect your GitHub repo
4. Render will read `render.yaml` and create the web service + PostgreSQL database automatically

### 9.3 Verify Deployment

After deployment:
- Backend URL: `https://sms-backend.onrender.com`
- Health check: `https://sms-backend.onrender.com/health`
- API docs: `https://sms-backend.onrender.com/docs`
## Step 10: Vercel Configuration (Detailed)

### 10.1 Create `vercel.json`

Create `vercel.json` at the repo root:

```json
{
  "buildCommand": "cd frontend && npm run build",
  "installCommand": "cd frontend && npm install",
  "outputDirectory": "frontend/.next",
  "framework": "nextjs",
  "rootDirectory": "frontend",
  "rewrites": [
    {
      "source": "/api/:path*",
      "destination": "${NEXT_PUBLIC_API_BASE_URL}/api/:path*"
    }
  ]
}
```

### 10.2 Import via Vercel Dashboard

1. Sign in at `https://vercel.com`
2. Click **Add New** -> **Import Git Repository**
3. Select `YOUR_USERNAME/sms`
4. Configure:

| Field | Value |
|-------|-------|
| **Project Name** | `sms-frontend` |
| **Root Directory** | `frontend` |
| **Framework Preset** | `Next.js` |

### 10.3 Environment Variables

Under **Settings > Environment**, add:

```env
NEXT_PUBLIC_API_BASE_URL=https://sms-backend.onrender.com
```

### 10.4 Custom Domain

1. In Vercel project, go to **Settings > Domains**
2. Add your apex domain: `yoursms.com`
3. Add wildcard domain: `*.yoursms.com`
4. Vercel will provide DNS records to add to your registrar
## Summary: Deployment Checklist

| # | Task | Platform | Status |
|---|------|----------|--------|
| 1 | Push repo to GitHub | GitHub | |
| 2 | Create Render web service | Render | |
| 3 | Set up PostgreSQL | Render | |
| 4 | Configure backend env vars | Render | |
| 5 | Import project to Vercel | Vercel | |
| 6 | Configure frontend env vars | Vercel | |
| 7 | Add wildcard domain | Vercel | |
| 8 | Point nameservers | Registrar | |
| 9 | Verify backend health | Render | |
| 10 | Verify frontend build | Vercel | |
| 11 | Create platform admin | CLI | |
| 12 | Create first tenant | Dashboard | |
| 13 | Invite school admin | Dashboard | |
| 14 | Test subdomain login | Browser | |

## Key Security Notes

1. **Never commit secrets**: `.env` files are in `.gitignore`
2. **JWT secret**: Must be >=32 characters, stored in environment variables only
3. **Platform administration**: Create platform administrators with `manage_platform_admin.py`; do not use a shared API key for super-admin access
4. **Tenant isolation**: Schema-per-tenant prevents cross-school data leaks
5. **Subdomain validation**: Client cannot override tenant via headers
6. **CORS**: Backend validates origin against configured domain suffix
