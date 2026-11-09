"use client";

import { useEffect, useMemo, useState } from "react";
import { Banknote, CalendarRange, CheckCheck, Plus, RefreshCw, Users } from "lucide-react";
import { schoolApi } from "../lib/school-api";
import FeedbackModal from "./FeedbackModal";

type StaffMember = {
  id: string;
  first_name: string;
  last_name: string;
  job_title: string;
  status: string;
};

type PayrollEntry = {
  id: string;
  staff_profile_id: string;
  staff_name: string;
  base_salary: number | string;
  allowances: number | string;
  deductions: number | string;
  net_pay: number | string;
  status: "draft" | "approved" | "paid";
  notes: string | null;
  created_at: string;
};

type PayrollRun = {
  id: string;
  label: string;
  pay_period_start: string;
  pay_period_end: string;
  status: "draft" | "approved" | "paid";
  total_amount: number | string;
  created_by_user_id: string | null;
  created_at: string;
  entries: PayrollEntry[];
};

type PayrollDashboard = {
  total_runs: number;
  total_payroll: number | string;
  paid_amount: number | string;
  pending_amount: number | string;
  active_staff: number;
  approved_entries: number;
};

type DraftEntry = {
  staff_profile_id: string;
  base_salary: string;
  allowances: string;
  deductions: string;
  notes: string;
};

function formatCurrency(value: number | string): string {
  const numeric = Number(value ?? 0);
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "NGN",
    maximumFractionDigits: 2,
  }).format(Number.isFinite(numeric) ? numeric : 0);
}

function formatDate(value: string): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export default function PayrollWorkspace() {
  const [dashboard, setDashboard] = useState<PayrollDashboard | null>(null);
  const [runs, setRuns] = useState<PayrollRun[]>([]);
  const [staff, setStaff] = useState<StaffMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<{ message: string; tone: "error" | "success" | "info" } | null>(null);
  const [label, setLabel] = useState("Monthly payroll");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [entries, setEntries] = useState<DraftEntry[]>([
    { staff_profile_id: "", base_salary: "0", allowances: "0", deductions: "0", notes: "" },
  ]);

  const summaryCards = useMemo(
    () => [
      { label: "Runs", value: dashboard ? String(dashboard.total_runs) : "0", accent: "purple" },
      { label: "Payroll total", value: dashboard ? formatCurrency(dashboard.total_payroll) : "₦0.00", accent: "orange" },
      { label: "Paid this cycle", value: dashboard ? formatCurrency(dashboard.paid_amount) : "₦0.00", accent: "green" },
      { label: "Pending", value: dashboard ? formatCurrency(dashboard.pending_amount) : "₦0.00", accent: "blue" },
    ],
    [dashboard],
  );

  async function refreshWorkspace() {
    const [dashboardData, runData, staffData] = await Promise.all([
      schoolApi<PayrollDashboard>("/api/v1/payroll/dashboard"),
      schoolApi<{ items: PayrollRun[]; total: number }>("/api/v1/payroll/runs"),
      schoolApi<StaffMember[]>("/api/v1/staff"),
    ]);

    setDashboard(dashboardData);
    setRuns(runData.items ?? []);
    setStaff(staffData);
  }

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        await refreshWorkspace();
      } catch (error: unknown) {
        if (!active) return;
        setNotice({
          message: error instanceof Error ? error.message : "Unable to load payroll data.",
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

  function updateEntry(index: number, field: keyof DraftEntry, value: string) {
    setEntries((current) =>
      current.map((entry, currentIndex) =>
        currentIndex === index ? { ...entry, [field]: value } : entry,
      ),
    );
  }

  function addEntry() {
    setEntries((current) => [
      ...current,
      { staff_profile_id: "", base_salary: "0", allowances: "0", deductions: "0", notes: "" },
    ]);
  }

  function removeEntry(index: number) {
    setEntries((current) => (current.length > 1 ? current.filter((_, currentIndex) => currentIndex !== index) : current));
  }

  async function createRun(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    try {
      const payloadEntries = entries
        .filter((entry) => entry.staff_profile_id)
        .map((entry) => ({
          staff_profile_id: entry.staff_profile_id,
          base_salary: Number(entry.base_salary || 0),
          allowances: Number(entry.allowances || 0),
          deductions: Number(entry.deductions || 0),
          notes: entry.notes.trim() || null,
        }));

      if (!payloadEntries.length) {
        throw new Error("At least one staff member must be included in the payroll run.");
      }

      await schoolApi<PayrollRun>("/api/v1/payroll/runs", {
        method: "POST",
        body: JSON.stringify({
          label: label.trim() || "Monthly payroll",
          pay_period_start: startDate,
          pay_period_end: endDate,
          entries: payloadEntries,
        }),
      });

      setLabel("Monthly payroll");
      setStartDate("");
      setEndDate("");
      setEntries([{ staff_profile_id: "", base_salary: "0", allowances: "0", deductions: "0", notes: "" }]);
      await refreshWorkspace();
      setNotice({ message: "Payroll run created successfully.", tone: "success" });
    } catch (error: unknown) {
      setNotice({
        message: error instanceof Error ? error.message : "Unable to create the payroll run.",
        tone: "error",
      });
    } finally {
      setSaving(false);
    }
  }

  async function approveRun(runId: string) {
    setSaving(true);
    try {
      await schoolApi<PayrollRun>(`/api/v1/payroll/runs/${runId}/approve`, {
        method: "POST",
      });
      await refreshWorkspace();
      setNotice({ message: "Payroll run approved.", tone: "success" });
    } catch (error: unknown) {
      setNotice({
        message: error instanceof Error ? error.message : "Unable to approve this payroll run.",
        tone: "error",
      });
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <section className="reports-state">Loading payroll workspace…</section>;
  }

  return (
    <div className="billing-workspace payroll-workspace">
      {notice ? (
        <FeedbackModal
          tone={notice.tone}
          title={notice.tone === "success" ? "Payroll update" : notice.tone === "error" ? "Payroll issue" : "Payroll notice"}
          message={notice.message}
          onClose={() => setNotice(null)}
        />
      ) : null}

      <div className="reports-toolbar panel">
        <div>
          <div className="eyebrow"><span className="sparkle">✳</span> PAYROLL</div>
          <h2>Staff payroll and approvals</h2>
        </div>
        <div className="reports-actions">
          <button className="button button-secondary" type="button" onClick={() => void refreshWorkspace()} disabled={saving}>
            <RefreshCw size={14} className={saving ? "analytics-refreshing" : ""} />
            Refresh
          </button>
        </div>
      </div>

      <section className="reports-stat-grid" aria-label="Payroll summary">
        {summaryCards.map((card) => (
          <article key={card.label} className={`panel reports-stat-card reports-${card.accent}`}>
            <span>{card.label}</span>
            <strong>{card.value}</strong>
          </article>
        ))}
      </section>

      <form className="panel payroll-form" onSubmit={createRun}>
        <div className="reports-filter-header"><span><Banknote size={15} /> Create payroll run</span></div>
        <div className="reports-filter-grid payroll-grid">
          <label>
            <span>Run label</span>
            <input value={label} onChange={(event) => setLabel(event.target.value)} placeholder="Monthly payroll" />
          </label>
          <label>
            <span>Start date</span>
            <input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
          </label>
          <label>
            <span>End date</span>
            <input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
          </label>
        </div>

        <div className="payroll-entry-list">
          {entries.map((entry, index) => (
            <div key={`entry-${index}`} className="payroll-entry-row">
              <label>
                <span>Staff member</span>
                <select value={entry.staff_profile_id} onChange={(event) => updateEntry(index, "staff_profile_id", event.target.value)}>
                  <option value="">Select staff</option>
                  {staff.map((person) => (
                    <option key={person.id} value={person.id}>{person.first_name} {person.last_name}</option>
                  ))}
                </select>
              </label>
              <label>
                <span>Basic salary</span>
                <input type="number" min="0" step="0.01" value={entry.base_salary} onChange={(event) => updateEntry(index, "base_salary", event.target.value)} />
              </label>
              <label>
                <span>Allowances</span>
                <input type="number" min="0" step="0.01" value={entry.allowances} onChange={(event) => updateEntry(index, "allowances", event.target.value)} />
              </label>
              <label>
                <span>Deductions</span>
                <input type="number" min="0" step="0.01" value={entry.deductions} onChange={(event) => updateEntry(index, "deductions", event.target.value)} />
              </label>
              <label>
                <span>Notes</span>
                <input value={entry.notes} onChange={(event) => updateEntry(index, "notes", event.target.value)} placeholder="Optional" />
              </label>
              <button type="button" className="button button-secondary compact-button" onClick={() => removeEntry(index)}>
                Remove
              </button>
            </div>
          ))}
        </div>

        <div className="payroll-form-actions">
          <button type="button" className="button button-secondary" onClick={addEntry}><Plus size={14} /> Add staff</button>
          <button type="submit" className="button button-primary" disabled={saving}>
            {saving ? "Saving…" : "Create payroll run"}
          </button>
        </div>
      </form>

      <section className="panel payroll-table-panel">
        <div className="reports-table-header">
          <div>
            <div className="eyebrow"><span className="sparkle">✳</span> PAYROLL ROSTER</div>
            <h3>Recent payroll runs</h3>
          </div>
        </div>

        <div className="table-wrap payroll-run-table">
          <table>
            <thead>
              <tr>
                <th>Run</th>
                <th>Period</th>
                <th>Total</th>
                <th>Status</th>
                <th>Entries</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {runs.length === 0 ? (
                <tr>
                  <td colSpan={6}>No payroll runs yet. Create the first payroll run for this school.</td>
                </tr>
              ) : (
                runs.map((run) => (
                  <tr key={run.id}>
                    <td>
                      <strong>{run.label}</strong>
                      <div className="muted-text">{formatDate(run.created_at)}</div>
                    </td>
                    <td>{formatDate(run.pay_period_start)} — {formatDate(run.pay_period_end)}</td>
                    <td>{formatCurrency(run.total_amount)}</td>
                    <td><span className={`pill pill-${run.status}`}>{run.status}</span></td>
                    <td>
                      {run.entries.length}
                      {run.entries.length > 0 && (
                        <div className="muted-text">
                          {run.entries.map((entry) => entry.staff_name).slice(0, 2).join(", ")}
                          {run.entries.length > 2 ? "…" : ""}
                        </div>
                      )}
                    </td>
                    <td>
                      {run.status !== "approved" && run.status !== "paid" ? (
                        <button className="button button-secondary compact-button" type="button" onClick={() => void approveRun(run.id)} disabled={saving}>
                          <CheckCheck size={14} /> Approve
                        </button>
                      ) : (
                        <span className="muted-text">{run.status === "paid" ? "Paid" : "Approved"}</span>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel payroll-details-panel">
        <div className="reports-filter-header"><span><CalendarRange size={15} /> Staff payroll detail</span></div>
        <div className="payroll-detail-list">
          {runs.flatMap((run) => run.entries).length === 0 ? (
            <p className="muted-text">No payroll entries have been created yet.</p>
          ) : (
            runs.flatMap((run) => run.entries.map((entry) => ({ ...entry, runLabel: run.label }))).map((entry) => (
              <div key={entry.id} className="payroll-detail-item">
                <div>
                  <strong>{entry.staff_name}</strong>
                  <div className="muted-text">{entry.runLabel}</div>
                </div>
                <div>{formatCurrency(entry.net_pay)}</div>
                <div className="muted-text">Base {formatCurrency(entry.base_salary)}</div>
                <div className="muted-text">Allow {formatCurrency(entry.allowances)} · Ded {formatCurrency(entry.deductions)}</div>
              </div>
            ))
          )}
        </div>
      </section>

      <section className="panel payroll-mini-status">
        <div className="reports-filter-header"><span><Users size={15} /> Summary</span></div>
        <div className="payroll-summary-grid">
          <div><strong>{dashboard?.active_staff ?? 0}</strong><span>Active staff</span></div>
          <div><strong>{dashboard?.approved_entries ?? 0}</strong><span>Approved entries</span></div>
          <div><strong>{dashboard?.paid_amount ? formatCurrency(dashboard.paid_amount) : "₦0.00"}</strong><span>Paid amount</span></div>
        </div>
      </section>
    </div>
  );
}
