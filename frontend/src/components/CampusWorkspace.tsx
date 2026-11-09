"use client";

import { useEffect, useMemo, useState } from "react";
import { Building2, MapPin, Phone, Plus, RefreshCw, School } from "lucide-react";
import { schoolApi } from "../lib/school-api";
import FeedbackModal from "./FeedbackModal";

type Campus = {
  id: string;
  name: string;
  code: string;
  address: string | null;
  phone: string | null;
  email: string | null;
  is_primary: boolean;
  status: "active" | "inactive";
  created_at: string;
  updated_at: string;
};

function formatDate(value: string): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export default function CampusWorkspace() {
  const [campuses, setCampuses] = useState<Campus[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<{ message: string; tone: "error" | "success" | "info" } | null>(null);
  const [form, setForm] = useState({
    name: "",
    code: "",
    address: "",
    phone: "",
    email: "",
    is_primary: false,
    status: "active" as "active" | "inactive",
  });

  const summaryCards = useMemo(
    () => [
      { label: "Total campuses", value: String(campuses.length), accent: "purple" },
      { label: "Primary campus", value: campuses.find((campus) => campus.is_primary)?.name ?? "—", accent: "green" },
      { label: "Active", value: String(campuses.filter((campus) => campus.status === "active").length), accent: "blue" },
      { label: "Inactive", value: String(campuses.filter((campus) => campus.status === "inactive").length), accent: "orange" },
    ],
    [campuses],
  );

  async function refreshWorkspace() {
    const data = await schoolApi<Campus[]>("/api/v1/campuses");
    setCampuses(data ?? []);
  }

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        await refreshWorkspace();
      } catch (error: unknown) {
        if (!active) return;
        setNotice({
          message: error instanceof Error ? error.message : "Unable to load campus data.",
          tone: "error",
        });
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  async function submitCampus(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    try {
      await schoolApi<Campus>("/api/v1/campuses", {
        method: "POST",
        body: JSON.stringify({
          name: form.name.trim(),
          code: form.code.trim(),
          address: form.address.trim() || null,
          phone: form.phone.trim() || null,
          email: form.email.trim() || null,
          is_primary: form.is_primary,
          status: form.status,
        }),
      });
      setForm({
        name: "",
        code: "",
        address: "",
        phone: "",
        email: "",
        is_primary: false,
        status: "active",
      });
      await refreshWorkspace();
      setNotice({ message: "Campus added successfully.", tone: "success" });
    } catch (error: unknown) {
      setNotice({
        message: error instanceof Error ? error.message : "Unable to create the campus.",
        tone: "error",
      });
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <section className="reports-state">Loading campus workspace…</section>;
  }

  return (
    <div className="billing-workspace learning-workspace">
      {notice ? (
        <FeedbackModal
          tone={notice.tone}
          title={notice.tone === "success" ? "Campus update" : notice.tone === "error" ? "Campus issue" : "Campus notice"}
          message={notice.message}
          onClose={() => setNotice(null)}
        />
      ) : null}

      <div className="reports-toolbar panel">
        <div>
          <div className="eyebrow"><span className="sparkle">✳</span> CAMPUSES</div>
          <h2>Multi-campus administration</h2>
        </div>
        <div className="reports-actions">
          <button className="button button-secondary" type="button" onClick={() => void refreshWorkspace()} disabled={saving}>
            <RefreshCw size={14} className={saving ? "analytics-refreshing" : ""} />
            Refresh
          </button>
        </div>
      </div>

      <section className="reports-stat-grid" aria-label="Campus summary">
        {summaryCards.map((card) => (
          <article key={card.label} className={`panel reports-stat-card reports-${card.accent}`}>
            <span>{card.label}</span>
            <strong>{card.value}</strong>
          </article>
        ))}
      </section>

      <section className="workspace-grid two-column">
        <form onSubmit={submitCampus} className="panel workspace-form">
          <div className="panel-header"><Building2 size={18} /> <span>Create campus</span></div>
          <label>
            <span>Campus name</span>
            <input value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} placeholder="Main Campus" required />
          </label>
          <div className="stacked-fields">
            <label>
              <span>Campus code</span>
              <input value={form.code} onChange={(event) => setForm((current) => ({ ...current, code: event.target.value }))} placeholder="MAIN" required />
            </label>
            <label>
              <span>Status</span>
              <select value={form.status} onChange={(event) => setForm((current) => ({ ...current, status: event.target.value as "active" | "inactive" }))}>
                <option value="active">Active</option>
                <option value="inactive">Inactive</option>
              </select>
            </label>
          </div>
          <label>
            <span>Address</span>
            <input value={form.address} onChange={(event) => setForm((current) => ({ ...current, address: event.target.value }))} placeholder="123 Academy Road" />
          </label>
          <div className="stacked-fields">
            <label>
              <span>Phone</span>
              <input value={form.phone} onChange={(event) => setForm((current) => ({ ...current, phone: event.target.value }))} placeholder="+234 800 000 0000" />
            </label>
            <label>
              <span>Email</span>
              <input type="email" value={form.email} onChange={(event) => setForm((current) => ({ ...current, email: event.target.value }))} placeholder="campus@school.org" />
            </label>
          </div>
          <label className="checkbox-row">
            <input type="checkbox" checked={form.is_primary} onChange={(event) => setForm((current) => ({ ...current, is_primary: event.target.checked }))} />
            <span>Set as primary campus</span>
          </label>
          <button className="button button-primary" type="submit" disabled={saving || !form.name.trim() || !form.code.trim()}>
            {saving ? "Saving..." : "Add campus"}
          </button>
        </form>

        <div className="panel">
          <div className="panel-header"><School size={18} /> <span>Campus directory</span></div>
          {campuses.length === 0 ? (
            <p className="empty-state">No campuses have been registered yet.</p>
          ) : (
            <div className="list-stack">
              {campuses.map((campus) => (
                <article key={campus.id} className="data-card">
                  <div className="data-card-header">
                    <div>
                      <strong>{campus.name}</strong>
                      <small>{campus.code}</small>
                    </div>
                    <span className={`status-badge ${campus.status}`}>{campus.status}</span>
                  </div>
                  <div className="meta-grid">
                    <span><MapPin size={14} /> {campus.address || "Address unavailable"}</span>
                    <span><Phone size={14} /> {campus.phone || "No phone"}</span>
                  </div>
                  <div className="meta-row">
                    <span>{campus.email || "No email"}</span>
                    <span>{campus.is_primary ? "Primary campus" : "Satellite campus"}</span>
                  </div>
                  <div className="meta-row subtle">
                    <span>Created {formatDate(campus.created_at)}</span>
                    <span>Updated {formatDate(campus.updated_at)}</span>
                  </div>
                </article>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
