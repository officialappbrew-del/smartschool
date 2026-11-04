"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, BarChart3, BookOpen, GraduationCap, RefreshCw, Users, Wallet } from "lucide-react";
import { schoolApi } from "../lib/school-api";

type AnalyticsDashboard = {
  active_students: number;
  active_enrollments: number;
  active_classes: number;
  enrollment_trend: { month: string; enrollments: number }[];
  attendance: {
    rate: number | string | null;
    marked_count: number;
    present_count: number;
    late_count: number;
    absent_count: number;
    excused_count: number;
    trend: { month: string; rate: number | string | null; marked_count: number }[];
  };
  fee_summaries: {
    currency: string;
    total_invoiced: number | string;
    total_collected: number | string;
    total_outstanding: number | string;
    collection_rate: number | string;
    invoice_count: number;
  }[];
  grade_distribution: { band: string; count: number }[];
  average_grade: number | string | null;
  graded_count: number;
};

function numericValue(value: number | string | null): number {
  if (value === null) return 0;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function monthLabel(value: string): string {
  const [year, month] = value.split("-").map(Number);
  return new Intl.DateTimeFormat(undefined, { month: "short" }).format(new Date(year, month - 1, 1));
}

function compactNumber(value: number): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(value);
}

function currencyValue(value: number | string, currency: string): string {
  return `${currency} ${new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(numericValue(value))}`;
}

export default function AnalyticsWorkspace() {
  const [data, setData] = useState<AnalyticsDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const loadDashboard = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    setError("");
    try {
      if (!window.sessionStorage.getItem("northstar_access_token")) {
        window.location.replace("/login");
        return;
      }
      const account = await schoolApi<{ role: string }>("/api/v1/auth/me");
      if (account.role !== "school_admin") {
        window.location.replace(account.role === "teacher" ? "/" : "/parent");
        return;
      }
      setData(await schoolApi<AnalyticsDashboard>("/api/v1/analytics/dashboard"));
    } catch (loadError: unknown) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load school analytics.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void loadDashboard();
  }, [loadDashboard]);

  if (loading) {
    return <section className="analytics-state" aria-live="polite">Loading school analytics…</section>;
  }

  if (!data) {
    return (
      <section className="panel analytics-error" role="alert">
        <p>{error || "Analytics are currently unavailable."}</p>
        <button className="button button-secondary" type="button" onClick={() => void loadDashboard(true)} disabled={refreshing}>
          <RefreshCw size={14} /> Try again
        </button>
      </section>
    );
  }

  const maxEnrollment = Math.max(1, ...data.enrollment_trend.map((item) => item.enrollments));
  const gradeTotal = data.grade_distribution.reduce((total, item) => total + item.count, 0);

  return (
    <div className="analytics-workspace">
      <div className="analytics-toolbar">
        <p>Tenant data · rolling 12 months where applicable</p>
        <button className="button button-secondary" type="button" onClick={() => void loadDashboard(true)} disabled={refreshing}>
          <RefreshCw size={14} className={refreshing ? "analytics-refreshing" : ""} />
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      <section className="analytics-stat-grid" aria-label="School summary">
        <article className="panel analytics-stat-card">
          <span className="analytics-stat-icon analytics-purple"><Users size={17} /></span>
          <span className="analytics-stat-label">Active students</span>
          <strong>{compactNumber(data.active_students)}</strong>
          <small>{compactNumber(data.active_enrollments)} active class enrollments</small>
        </article>
        <article className="panel analytics-stat-card">
          <span className="analytics-stat-icon analytics-green"><Activity size={17} /></span>
          <span className="analytics-stat-label">Attendance rate</span>
          <strong>{data.attendance.rate === null ? "—" : `${compactNumber(numericValue(data.attendance.rate))}%`}</strong>
          <small>{compactNumber(data.attendance.marked_count)} marks in the last 12 months</small>
        </article>
        <article className="panel analytics-stat-card">
          <span className="analytics-stat-icon analytics-blue"><GraduationCap size={17} /></span>
          <span className="analytics-stat-label">Active classes</span>
          <strong>{compactNumber(data.active_classes)}</strong>
          <small>Across the school</small>
        </article>
        <article className="panel analytics-stat-card">
          <span className="analytics-stat-icon analytics-orange"><BookOpen size={17} /></span>
          <span className="analytics-stat-label">Average grade</span>
          <strong>{data.average_grade === null ? "—" : `${compactNumber(numericValue(data.average_grade))}%`}</strong>
          <small>{compactNumber(data.graded_count)} recorded scores</small>
        </article>
      </section>

      <section className="analytics-grid">
        <article className="panel analytics-card analytics-enrollment">
          <header className="analytics-card-header">
            <div><h2><Users size={16} /> Enrollment trend</h2><p>Active enrollments by month</p></div>
            <span className="analytics-period">12 months</span>
          </header>
          {data.enrollment_trend.some((item) => item.enrollments > 0) ? (
            <div className="analytics-bars" role="img" aria-label="Monthly active enrollment counts for the last 12 months">
              {data.enrollment_trend.map((item) => (
                <div className="analytics-bar-column" key={item.month} title={`${item.month}: ${item.enrollments} enrollments`}>
                  <span className="analytics-bar-value">{item.enrollments || ""}</span>
                  <div className="analytics-bar-track"><i style={{ height: `${Math.max(3, item.enrollments / maxEnrollment * 100)}%` }} /></div>
                  <span className="analytics-bar-label">{monthLabel(item.month)}</span>
                </div>
              ))}
            </div>
          ) : <p className="analytics-empty">No active enrollment activity in this period.</p>}
        </article>

        <article className="panel analytics-card">
          <header className="analytics-card-header">
            <div><h2><Activity size={16} /> Attendance rate</h2><p>Present and late marks; excused entries excluded</p></div>
            <span className="analytics-period">12 months</span>
          </header>
          <div className="analytics-attendance-list">
            {data.attendance.trend.map((item) => {
              const rate = item.rate === null ? null : numericValue(item.rate);
              return (
                <div className="analytics-attendance-row" key={item.month}>
                  <span>{monthLabel(item.month)}</span>
                  <div className="analytics-progress-track"><i style={{ width: `${rate === null ? 0 : Math.min(100, Math.max(0, rate))}%` }} /></div>
                  <strong>{rate === null ? "—" : `${compactNumber(rate)}%`}</strong>
                  <small>{item.marked_count}</small>
                </div>
              );
            })}
          </div>
          <div className="analytics-attendance-breakdown">
            <span><i className="analytics-dot analytics-dot-green" /> Present <strong>{data.attendance.present_count}</strong></span>
            <span><i className="analytics-dot analytics-dot-purple" /> Late <strong>{data.attendance.late_count}</strong></span>
            <span><i className="analytics-dot analytics-dot-gray" /> Absent <strong>{data.attendance.absent_count}</strong></span>
            <span><i className="analytics-dot analytics-dot-orange" /> Excused <strong>{data.attendance.excused_count}</strong></span>
          </div>
        </article>

        <article className="panel analytics-card">
          <header className="analytics-card-header">
            <div><h2><Wallet size={16} /> Fee collection</h2><p>All non-voided invoices and payments</p></div>
          </header>
          {data.fee_summaries.length ? (
            <div className="analytics-fees">
              {data.fee_summaries.map((item) => {
                const rate = numericValue(item.collection_rate);
                return (
                  <div className="analytics-fee-row" key={item.currency}>
                    <div className="analytics-fee-heading">
                      <strong>{item.currency}</strong>
                      <span>{compactNumber(rate)}% collected</span>
                    </div>
                    <div className="analytics-progress-track analytics-fee-track"><i style={{ width: `${Math.min(100, Math.max(0, rate))}%` }} /></div>
                    <div className="analytics-fee-totals">
                      <span><small>Collected</small><strong>{currencyValue(item.total_collected, item.currency)}</strong></span>
                      <span><small>Outstanding</small><strong>{currencyValue(item.total_outstanding, item.currency)}</strong></span>
                      <span><small>Invoiced</small><strong>{currencyValue(item.total_invoiced, item.currency)}</strong></span>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : <p className="analytics-empty">No fee invoices have been issued yet.</p>}
        </article>

        <article className="panel analytics-card">
          <header className="analytics-card-header">
            <div><h2><BarChart3 size={16} /> Grade distribution</h2><p>All recorded assessment scores</p></div>
            <span className="analytics-period">{compactNumber(gradeTotal)} scores</span>
          </header>
          {gradeTotal ? (
            <div className="analytics-grade-list">
              {data.grade_distribution.map((item) => (
                <div className="analytics-grade-row" key={item.band}>
                  <span>{item.band}%</span>
                  <div className="analytics-progress-track"><i style={{ width: `${item.count / gradeTotal * 100}%` }} /></div>
                  <strong>{item.count}</strong>
                </div>
              ))}
            </div>
          ) : <p className="analytics-empty">No scores have been recorded yet.</p>}
        </article>
      </section>
      <p className="analytics-footnote">Trends cover the current month and preceding 11 calendar months. Grade distribution includes all saved scores; fee totals exclude voided invoices and payments.</p>
    </div>
  );
}
