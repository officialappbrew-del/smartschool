"use client";

import {
  Activity,
  ArrowUpRight,
  Building2,
  Check,
  ChevronLeft,
  ChevronRight,
  LoaderCircle,
  LogOut,
  Mail,
  Plus,
  RefreshCw,
  ShieldCheck,
  X,
} from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import FeedbackModal from "./FeedbackModal";

type Tenant = {
  identifier: string;
  name: string;
  status: string;
  migration_status: "current" | "behind" | "not_migrated" | "unknown";
  current_revision: string | null;
  head_revision: string;
};

type TenantPage = {
  items: Tenant[];
  total: number;
};

type ActivityEvent = {
  id: string;
  admin_email: string;
  action: string;
  tenant_identifier: string | null;
  summary: string;
  occurred_at: string;
};

const PAGE_SIZE = 50;
const SESSION_STORAGE_KEY = "northstar_platform_access_token";

async function platformApi<T>(
  path: string,
  token: string | null,
  init: RequestInit = {},
): Promise<T> {
  const apiBase = new URL(
    process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000",
  );
  const headers = new Headers(init.headers);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(new URL(path, apiBase), { ...init, headers });
  if (response.status === 401 && token) {
    window.sessionStorage.removeItem(SESSION_STORAGE_KEY);
    window.dispatchEvent(new Event("northstar-platform-session-expired"));
  }
  let result: unknown;
  try {
    result = await response.json();
  } catch {
    throw new Error("The platform service returned an invalid response.");
  }
  if (!response.ok) {
    const body = result as { detail?: unknown };
    const message =
      typeof body.detail === "string"
        ? body.detail
        : "The request could not be completed.";
    throw new Error(message);
  }
  return result as T;
}

function messageFrom(error: unknown): string {
  return error instanceof Error ? error.message : "The request could not be completed.";
}

export default function PlatformAdminDashboard() {
  const [emailInput, setEmailInput] = useState("");
  const [passwordInput, setPasswordInput] = useState("");
  const [adminToken, setAdminToken] = useState<string | null>(null);
  const [adminEmail, setAdminEmail] = useState("");
  const [checkingSession, setCheckingSession] = useState(true);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [activity, setActivity] = useState<ActivityEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pendingSuspension, setPendingSuspension] = useState<Tenant | null>(null);
  const [name, setName] = useState("");
  const [identifier, setIdentifier] = useState("");
  const [inviteEmails, setInviteEmails] = useState<Record<string, string>>({});

  async function loadDashboard(token: string, pageOffset: number): Promise<void> {
    const [tenantResult, activityResult] = await Promise.all([
      platformApi<TenantPage>(
        `/api/v1/admin/tenants?skip=${pageOffset}&limit=${PAGE_SIZE}`,
        token,
      ),
      platformApi<{ items: ActivityEvent[] }>("/api/v1/admin/activity?limit=8", token),
    ]);
    setTenants(tenantResult.items);
    setTotal(tenantResult.total);
    setOffset(pageOffset);
    setActivity(activityResult.items);
  }

  useEffect(() => {
    const expireSession = () => {
      setAdminToken(null);
      setAdminEmail("");
      setTenants([]);
      setActivity([]);
      setError("Your platform session expired. Sign in again.");
    };
    window.addEventListener("northstar-platform-session-expired", expireSession);

    const savedToken = window.sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (!savedToken) {
      setCheckingSession(false);
    } else {
      void loadDashboard(savedToken, 0)
        .then(() => setAdminToken(savedToken))
        .catch(() => {
          window.sessionStorage.removeItem(SESSION_STORAGE_KEY);
          setAdminToken(null);
        })
        .finally(() => setCheckingSession(false));
    }

    return () => {
      window.removeEventListener("northstar-platform-session-expired", expireSession);
    };
  }, []);

  async function unlock(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("unlock");
    setError("");
    try {
      const result = await platformApi<{ access_token: string; email: string }>(
        "/api/v1/admin/auth/login",
        null,
        {
          method: "POST",
          body: JSON.stringify({ email: emailInput, password: passwordInput }),
        },
      );
      await loadDashboard(result.access_token, 0);
      window.sessionStorage.setItem(SESSION_STORAGE_KEY, result.access_token);
      setAdminToken(result.access_token);
      setAdminEmail(result.email);
      setPasswordInput("");
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setBusy("");
    }
  }

  function signOut() {
    window.sessionStorage.removeItem(SESSION_STORAGE_KEY);
    setAdminToken(null);
    setAdminEmail("");
    setTenants([]);
    setActivity([]);
    setError("");
    setNotice("");
  }

  async function refresh(pageOffset = offset) {
    if (!adminToken) return;
    setBusy("refresh");
    setError("");
    try {
      await loadDashboard(adminToken, pageOffset);
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setBusy("");
    }
  }

  async function createTenant(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!adminToken) return;
    setBusy("create");
    setError("");
    setNotice("");
    try {
      const created = await platformApi<{ identifier: string; name: string }>(
        "/api/v1/admin/tenants",
        adminToken,
        {
          method: "POST",
          body: JSON.stringify({ identifier, name }),
        },
      );
      setIdentifier("");
      setName("");
      try {
        await platformApi(
          `/api/v1/admin/tenants/${encodeURIComponent(created.identifier)}/migrations`,
          adminToken,
          { method: "POST" },
        );
        setNotice(`${created.name} was created and its database is ready.`);
      } catch (migrationError) {
        setError(
          `${created.name} was created, but its database migration failed: ${messageFrom(migrationError)}`,
        );
      }
      await loadDashboard(adminToken, 0);
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setBusy("");
    }
  }

  async function migrateTenant(tenant: Tenant) {
    if (!adminToken) return;
    setBusy(`migrate:${tenant.identifier}`);
    setError("");
    setNotice("");
    try {
      const result = await platformApi<{ message: string }>(
        `/api/v1/admin/tenants/${encodeURIComponent(tenant.identifier)}/migrations`,
        adminToken,
        { method: "POST" },
      );
      setNotice(`${tenant.name}: ${result.message}`);
      await loadDashboard(adminToken, offset);
      await loadDashboard(adminToken, offset);
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setBusy("");
    }
  }

  async function inviteAdmin(event: FormEvent<HTMLFormElement>, tenant: Tenant) {
    event.preventDefault();
    if (!adminToken) return;
    const email = inviteEmails[tenant.identifier]?.trim();
    if (!email) return;
    setBusy(`invite:${tenant.identifier}`);
    setError("");
    setNotice("");
    try {
      const result = await platformApi<{ message: string }>(
        `/api/v1/admin/tenants/${encodeURIComponent(tenant.identifier)}/admin-invitations`,
        adminToken,
        { method: "POST", body: JSON.stringify({ email }) },
      );
      setNotice(`${tenant.name}: ${result.message}`);
      setInviteEmails((current) => ({ ...current, [tenant.identifier]: "" }));
      await loadDashboard(adminToken, offset);
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setBusy("");
    }
  }

  async function setTenantStatus(tenant: Tenant) {
    if (!adminToken) return;
    const nextStatus = tenant.status === "suspended" ? "active" : "suspended";
    setBusy(`status:${tenant.identifier}`);
    setError("");
    setNotice("");
    try {
      await platformApi(
        `/api/v1/admin/tenants/${encodeURIComponent(tenant.identifier)}/status`,
        adminToken,
        { method: "PATCH", body: JSON.stringify({ status: nextStatus }) },
      );
      await loadDashboard(adminToken, offset);
      setNotice(`${tenant.name} is now ${nextStatus}.`);
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setBusy("");
    }
  }

  if (checkingSession) {
    return <div className="workspace-loading">Checking platform session...</div>;
  }

  if (!adminToken) {
    return (
      <>
      <main className="platform-admin-login">
        <form className="platform-admin-login-card" onSubmit={unlock}>
          <div className="platform-admin-brand">
            <span className="platform-admin-brand-icon"><ShieldCheck size={23} /></span>
            <span><strong>Northstar</strong><small>PLATFORM ADMIN</small></span>
          </div>
          <div className="platform-admin-login-title">
            <h1>Platform control</h1>
            <p>Sign in with your platform administrator account.</p>
          </div>
          <label className="platform-admin-field">
            Email
            <input
              type="email"
              autoComplete="username"
              value={emailInput}
              onChange={(event) => setEmailInput(event.target.value)}
              required
            />
          </label>
          <label className="platform-admin-field">
            Password
            <input
              type="password"
              autoComplete="current-password"
              value={passwordInput}
              onChange={(event) => setPasswordInput(event.target.value)}
              required
            />
          </label>
          <button className="platform-admin-primary" type="submit" disabled={busy === "unlock"}>
            {busy === "unlock" ? <LoaderCircle className="is-spinning" size={16} /> : "Continue"}
          </button>
          <p className="platform-admin-security">Your session expires after 30 minutes and is limited to this browser tab.</p>
        </form>
      </main>
      <FeedbackModal message={error} tone="error" onClose={() => setError("")} />
      </>
    );
  }

  const activeCount = tenants.filter((tenant) => tenant.status === "active").length;

  return (
    <>
    <main className="platform-admin">
      <header className="platform-admin-topbar">
        <div className="platform-admin-brand">
          <span className="platform-admin-brand-icon"><ShieldCheck size={23} /></span>
          <span><strong>Northstar</strong><small>PLATFORM ADMIN</small></span>
        </div>
        <div className="platform-admin-topbar-actions">
          <span className="platform-admin-secure"><span /> {adminEmail}</span>
          <button className="platform-admin-quiet-button" onClick={signOut}>
            <LogOut size={15} /> Sign out
          </button>
        </div>
      </header>

      <div className="platform-admin-content">
        <div className="platform-admin-layout">
          <div className="platform-admin-main-column">
            <div className="platform-admin-heading">
              <div>
                <div className="platform-admin-eyebrow">PLATFORM OVERVIEW</div>
                <h1>Tenant management</h1>
                <p>Create school workspaces, prepare their databases, and invite school administrators.</p>
              </div>
              <button className="platform-admin-quiet-button" onClick={() => void refresh()} disabled={busy !== ""}>
                <RefreshCw size={15} className={busy === "refresh" ? "is-spinning" : ""} /> Refresh
              </button>
            </div>

            <section className="platform-admin-stats" aria-label="Tenant statistics">
              <article><span>Total tenants</span><strong>{total}</strong><small>Registered school workspaces</small></article>
              <article><span>Active on this page</span><strong>{activeCount}</strong><small>Available for school sign-in</small></article>
              <article><span>Current page</span><strong>{total ? Math.floor(offset / PAGE_SIZE) + 1 : 0}</strong><small>{PAGE_SIZE} tenants per page</small></article>
            </section>

            <section className="platform-admin-panel platform-admin-create">
              <div className="platform-admin-panel-heading">
                <span className="platform-admin-panel-icon"><Plus size={18} /></span>
                <div><h2>Create a school tenant</h2><p>Set its display name and unique URL slug. The database is migrated after creation.</p></div>
              </div>
              <form className="platform-admin-create-form" onSubmit={createTenant}>
                <label>
                  School name
                  <input value={name} onChange={(event) => setName(event.target.value)} maxLength={160} required placeholder="e.g. Ngogen Hospital School" />
                </label>
                <label>
                  Tenant URL slug
                  <span className="platform-admin-slug-input">
                    <input value={identifier} onChange={(event) => setIdentifier(event.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))} minLength={3} maxLength={48} pattern="[a-z0-9]+(?:-[a-z0-9]+)*" required placeholder="ngogenhospital" />
                    <span>.localhost:5173</span>
                  </span>
                </label>
                <button className="platform-admin-primary" type="submit" disabled={busy !== ""}>
                  {busy === "create" ? <LoaderCircle className="is-spinning" size={16} /> : <><Plus size={16} /> Create tenant</>}
                </button>
              </form>
            </section>

            <section className="platform-admin-panel platform-admin-tenant-panel">
              <div className="platform-admin-panel-heading platform-admin-list-heading">
                <span className="platform-admin-panel-icon"><Building2 size={18} /></span>
                <div><h2>All tenants</h2><p>Manage school access, database migrations, and administrator invitations.</p></div>
                <span className="platform-admin-total">{total} total</span>
              </div>
              <div className="platform-admin-tenant-list">
                {tenants.length === 0 ? (
                  <div className="platform-admin-empty">
                    <Building2 size={24} />
                    <strong>No tenants on this page</strong>
                    <span>Create the first school tenant above.</span>
                  </div>
                ) : tenants.map((tenant) => (
                  <article className="platform-admin-tenant" key={tenant.identifier}>
                    <div className="platform-admin-tenant-main">
                      <span className="platform-admin-tenant-avatar">{tenant.name.slice(0, 1).toUpperCase()}</span>
                      <div className="platform-admin-tenant-info">
                        <strong>{tenant.name}</strong>
                        <a href={`http://${tenant.identifier}.localhost:5173/`} target="_blank" rel="noreferrer">
                          {tenant.identifier}.localhost:5173 <ArrowUpRight size={12} />
                        </a>
                        <span className={`platform-admin-migration migration-${tenant.migration_status}`}>
                          Database: {tenant.migration_status === "current"
                            ? "Up to date"
                            : tenant.migration_status === "behind"
                              ? `Update available (${tenant.current_revision ?? "unknown"} -> ${tenant.head_revision})`
                              : tenant.migration_status === "not_migrated"
                                ? "Not migrated"
                                : "Status unavailable"}
                        </span>
                      </div>
                      <span className={`platform-admin-status status-${tenant.status}`}>{tenant.status}</span>
                    </div>
                    <div className="platform-admin-tenant-actions">
                      <form className="platform-admin-invite-form" onSubmit={(event) => void inviteAdmin(event, tenant)}>
                        <Mail size={15} />
                        <input
                          type="email"
                          aria-label={`Administrator email for ${tenant.name}`}
                          placeholder="School admin email"
                          value={inviteEmails[tenant.identifier] ?? ""}
                          onChange={(event) => setInviteEmails((current) => ({ ...current, [tenant.identifier]: event.target.value }))}
                          required
                        />
                        <button type="submit" disabled={busy !== "" || tenant.status !== "active"} title={tenant.status !== "active" ? "Reactivate this tenant before inviting an administrator" : "Send administrator invitation"}>
                          {busy === `invite:${tenant.identifier}` ? <LoaderCircle className="is-spinning" size={15} /> : "Invite admin"}
                        </button>
                      </form>
                      <button className="platform-admin-row-button" onClick={() => void migrateTenant(tenant)} disabled={busy !== ""}>
                        {busy === `migrate:${tenant.identifier}` ? <LoaderCircle className="is-spinning" size={15} /> : <RefreshCw size={14} />}
                        Migrate
                      </button>
                      <button className={`platform-admin-row-button ${tenant.status === "suspended" ? "is-reactivate" : "is-suspend"}`} onClick={() => tenant.status === "suspended" ? void setTenantStatus(tenant) : setPendingSuspension(tenant)} disabled={busy !== "" || tenant.status === "deleted"}>
                        {busy === `status:${tenant.identifier}` ? <LoaderCircle className="is-spinning" size={15} /> : tenant.status === "suspended" ? <Check size={14} /> : <X size={14} />}
                        {tenant.status === "suspended" ? "Reactivate" : "Suspend"}
                      </button>
                    </div>
                  </article>
                ))}
              </div>
              <div className="platform-admin-pagination">
                <span>{total ? `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)} of ${total} tenants` : "No tenants registered"}</span>
                <div>
                  <button onClick={() => void refresh(Math.max(0, offset - PAGE_SIZE))} disabled={busy !== "" || offset === 0} aria-label="Previous tenant page"><ChevronLeft size={17} /></button>
                  <button onClick={() => void refresh(offset + PAGE_SIZE)} disabled={busy !== "" || offset + PAGE_SIZE >= total} aria-label="Next tenant page"><ChevronRight size={17} /></button>
                </div>
              </div>
            </section>
            <p className="platform-admin-footnote">Platform administrator access is for trusted operators only.</p>
          </div>
          <aside className="platform-admin-activity-pane" aria-labelledby="platform-admin-activity-title">
            <div className="platform-admin-activity-heading">
              <span className="platform-admin-panel-icon"><Activity size={18} /></span>
              <div>
                <h2 id="platform-admin-activity-title">Activity log</h2>
                <p>Recent platform administrator actions</p>
              </div>
              <span className="platform-admin-activity-count">{activity.length}</span>
            </div>
            <div className="platform-admin-activity-list">
              {activity.length === 0 ? (
                <div className="platform-admin-empty">
                  <Activity size={22} />
                  <strong>No activity recorded yet</strong>
                  <span>Successful actions will appear here.</span>
                </div>
              ) : activity.map((event) => (
                <article className="platform-admin-activity-item" key={event.id}>
                  <span className={`platform-admin-activity-dot action-${event.action}`} aria-hidden="true" />
                  <div>
                    <strong>{event.summary}</strong>
                    <span>{event.admin_email}</span>
                    <time dateTime={event.occurred_at}>{new Date(event.occurred_at).toLocaleString()}</time>
                  </div>
                </article>
              ))}
            </div>
          </aside>
        </div>
      </div>
    </main>
    <FeedbackModal
      message={error || notice}
      tone={error ? "error" : "success"}
      onClose={() => {
        setError("");
        setNotice("");
      }}
    />
    <FeedbackModal
      message={pendingSuspension ? `Suspend ${pendingSuspension.name}? Its school users will lose access.` : ""}
      tone="confirm"
      title="Suspend tenant?"
      onClose={() => setPendingSuspension(null)}
      onConfirm={() => {
        if (pendingSuspension) void setTenantStatus(pendingSuspension);
        setPendingSuspension(null);
      }}
      confirmLabel="Suspend tenant"
      confirming={Boolean(pendingSuspension && busy === `status:${pendingSuspension.identifier}`)}
    />
    </>
  );
}
