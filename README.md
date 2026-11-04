# Northstar School Management

## Set up `ngogenhospital.localhost:5173`

This guide creates the `ngogenhospital` tenant, prepares its database schema, invites its first school administrator, and opens the school workspace locally.

### Requirements

- Python 3.11 or newer
- Node.js 20.9 or newer and npm
- PostgreSQL running locally
- SMTP access to send the administrator invitation

### 1. Configure the backend

From the project directory, create the Python environment and install dependencies:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Create `backend/.env` from the example only if it does not exist, so you do not overwrite existing credentials. Edit it and set:

```dotenv
TENANCY_DATABASE_URL=postgresql+asyncpg://northstar:pluralsight@localhost:5432/northstar
TENANCY_DOMAIN_SUFFIX=.localhost
TENANCY_JWT_SECRET=YOUR_RANDOM_SECRET_AT_LEAST_32_CHARACTERS
FRONTEND_ORIGIN=http://localhost:5173
PUBLIC_APP_BASE_URL=http://localhost:5173
```

The backend uses the async PostgreSQL driver automatically, including when this URL uses the standard `postgresql://` scheme. Restart the backend after changing `.env` so Uvicorn reloads these settings.

Create a PostgreSQL role and database if they do not already exist. Run these statements as a PostgreSQL administrator, replacing the password:

```sql
CREATE USER northstar WITH PASSWORD 'YOUR_DB_PASSWORD';
CREATE DATABASE northstar OWNER northstar;
```

The database role must be able to create schemas. Generate a random secret for the JWT signing key. Do not commit or share `.env`.

To email the first administrator, also configure `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_USE_SSL`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, and `DEFAULT_FROM_EMAIL` in `backend/.env`. For Gmail, use an App Password, not your regular account password.

### 2. Configure and start the frontend

Open a second PowerShell window:

```powershell
cd frontend
npm install
if (-not (Test-Path .env.local)) { Copy-Item .env.example .env.local }
```

The example API URL uses port `8000`. Start the frontend on the requested port:

```powershell
npm run dev -- --port 5173
```

### 3. Start the backend

In the first PowerShell window, from `backend` with `.venv` active:

```powershell
uvicorn app.main:app --env-file .env --reload --port 8000
```

The app also loads `backend/.env` automatically, but `--env-file .env` makes the configuration explicit. Keep both servers running. The backend health check is `http://localhost:8000/health`.

### 4. Create your first platform administrator

In another PowerShell window:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python manage_platform_admin.py create
```

Enter the operator email and a strong password when prompted. The password is hashed before it is stored; it is not saved in `.env` or passed on the command line. The command creates the `platform_admins` table in the configured PostgreSQL database if it does not exist. You can run it again to add another platform administrator; each email must be unique.

### 5. Create and set up tenants in the super-admin dashboard

Open **http://admin.localhost:5173/** and sign in with the platform administrator email and password. The dashboard keeps a short-lived access token in the current browser tab; sessions expire after 30 minutes and are rechecked against the active platform admin account on each request.

Use **Create a school tenant** to enter a school name and a URL slug, such as `ngogenhospital`. The dashboard creates the tenant and runs its database migrations. In the tenant list, enter the first school administrator's email and choose **Invite admin**. SMTP must be configured first; the invitation link expires after 48 hours.

The school administrator follows the emailed link to choose a password, then signs in at **http://ngogenhospital.localhost:5173/login**. The school workspace is **http://ngogenhospital.localhost:5173/**.

The tenant list also shows each school's current database migration status, lets the platform super admin rerun migrations, suspend a school, and reactivate it. The recent activity panel records successful platform-admin sign-ins and tenant setup actions. Platform administrator passwords are separate from school administrator accounts.

To give a parent access, add the guardian's email address to the student's record. In the **Students** list, select the envelope icon on that student's row to send a one-time invitation. The guardian sets a password from the email link and signs in at the school's `/login` page; parent accounts open the read-only `/parent` portal, where they can view linked students' attendance and term report cards.

To set up teacher access, open **Classes** as a school administrator, invite the teacher by email, and assign one or more class sections. After accepting the invitation, teachers sign in at `http://ngogenhospital.localhost:5173/login` and land on their teacher dashboard. It lists assigned classes, links to attendance and grade entry, and shows the teacher's weekly timetable. Offline attendance and grade changes are saved in that browser's local storage and sync when the connection returns; load the class roster or gradebook while online at least once before working offline.

## Configure tenant fees

1. In `backend/.env`, set `BILLING_ENCRYPTION_KEY` to a new key generated from the backend virtual environment:

   ```powershell
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   Keep this key private and persistent. Existing gateway credentials cannot be decrypted if the key changes.
2. Restart the backend. For an existing tenant, use the platform super-admin dashboard's **Run migrations** action to apply the latest tenant tables; newly created tenants receive the current migrations automatically. Then sign in at `http://ngogenhospital.localhost:5173/login` as a school administrator and open **Fees**.
3. Choose that school's payment provider and currency, then add a fee for a class and term. Generate invoices for an active class enrollment and record cash or bank-transfer payments from the invoice list.
4. Parents can view their linked students' invoices, balances, and recorded payments in the `/parent` portal.

Billing settings, fees, invoices, and payments are isolated per tenant. Invoice currency is fixed when the invoice is generated. Gateway credentials are encrypted and never returned by the API.

For online payments, save the provider's secret key under **Fees**. Configure each tenant's provider webhook URL as `https://<tenant-api-domain>/api/v1/billing/webhooks/paystack` or `/api/v1/billing/webhooks/stripe`. Stripe must send `checkout.session.completed` and `checkout.session.async_payment_succeeded`; save the Stripe endpoint signing secret in the tenant settings. Paystack's webhook signature is verified with its secret key. Use HTTPS for live provider callbacks. Successful callbacks are signature-checked and idempotent before a payment is recorded.

## School announcements

School administrators can open **Announcements** in the school workspace to save drafts and publish school-wide updates. Publishing makes the update visible in the `/parent` portal and emails each distinct guardian address on an active student record. SMTP must be configured using the existing email environment variables before publishing to recipients. Delivery results are tracked per recipient, and failed or pending messages can be retried from the announcement.

Existing tenants must run the latest migrations from the platform super-admin dashboard to create the announcement and delivery-tracking tables; newly created tenants receive these migrations automatically.

## Teacher-parent messaging

Parents can open **Messages** in the parent portal and start a private, threaded conversation with a teacher assigned to their child's active class. Teachers can reply from **Messages** in their workspace. Either participant can flag the other participant's message with a reason; school administrators review open flags under **Message moderation**. Message access is limited to the guardian account linked to the student and teachers currently assigned to the student's active class. Existing tenants must run the latest migration from the platform super-admin dashboard.

## Timetable management

School administrators can open **Timetable** from the workspace or **Calendar** in the management menu to add, edit, and remove weekly lessons. Each lesson needs an active class, an assigned active teacher, subject, weekday, and start/end time; room is optional. The API rejects overlapping lessons for the same class, teacher, or room. Teachers see their assigned lessons under **My timetable**. Existing tenants must run the latest migrations from the platform super-admin dashboard.

## Staff records

School administrators can open **Staff records** to maintain personnel profiles, mark staff active or inactive, and record qualifications and contracts. A profile may optionally link to an existing teacher account in the same school; non-teaching employees can have a profile without a login. Staff numbers and linked teacher accounts must be unique within the school. Contract records support draft, active, and ended statuses and validate their date ranges. Existing tenants must run the latest migration from the platform super-admin dashboard before using this feature.

## School analytics

The school overview and **Analytics** page show live tenant metrics: active student and enrollment counts, monthly enrollment and attendance trends for the last 12 calendar months, fee collection totals by currency, and the distribution and average of saved grades. Attendance rates include present and late marks and exclude excused marks. Grade distribution includes all saved scores; fee analytics omit voided invoices and payments. This feature reads the existing tenant data and does not require an additional migration.

## Common commands

Run all backend tests:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m unittest discover -s tests
```

Check or build the frontend:

```powershell
cd frontend
npm run typecheck
npm run build
```

The API's interactive documentation is available at `http://localhost:8000/docs`. Use it for the implemented student, class, attendance, grade, report-card, and manual billing endpoints.
