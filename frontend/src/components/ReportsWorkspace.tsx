"use client";

import { useEffect, useMemo, useState } from "react";
import { BarChart3, Download, Filter, RefreshCw } from "lucide-react";
import { schoolApi } from "../lib/school-api";
import FeedbackModal from "./FeedbackModal";

type ClassOption = {
  id: string;
  name: string;
  academic_year: string;
};

type ReportSummary = {
  total_students: number;
  attendance_rate: number | null;
  average_grade: number | null;
  total_fees_collected: number;
  total_fees_outstanding: number;
  collection_rate: number;
};

type ReportRow = {
  student_id: string;
  student_name: string;
  admission_number: string;
  class_name: string;
  attendance_rate: number | null;
  average_grade: number | null;
  fees_outstanding: number;
  fees_collected: number;
  status: string;
};

type ReportPayload = {
  summary: ReportSummary;
  rows: ReportRow[];
};

function formatCurrency(value: number): string {
  return new Intl.NumberFormat(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

function percentileLabel(value: number | null): string {
  if (value === null || Number.isNaN(value)) return "—";
  return `${value.toFixed(1)}%`;
}

export default function ReportsWorkspace() {
  const [classes, setClasses] = useState<ClassOption[]>([]);
  const [report, setReport] = useState<ReportPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<{ message: string; tone: "error" | "success" | "info" } | null>(null);
  const [filters, setFilters] = useState({
    class_section_id: "",
    start_date: "",
    end_date: "",
  });

  const summaryCards = useMemo(
    () => [
      { label: "Students", value: report ? String(report.summary.total_students) : "—", accent: "purple" },
      { label: "Attendance rate", value: report ? percentileLabel(report.summary.attendance_rate) : "—", accent: "green" },
      { label: "Average grade", value: report ? percentileLabel(report.summary.average_grade) : "—", accent: "blue" },
      { label: "Collection rate", value: report ? percentileLabel(report.summary.collection_rate) : "—", accent: "orange" },
    ],
    [report],
  );

  const statusClassName = (status: string) => {
    const normalized = status.toLowerCase().replace(/\s+/g, "-");
    return normalized === "on-track" || normalized === "watchlist" || normalized === "at-risk" || normalized === "no-data"
      ? normalized
      : "no-data";
  };

  async function loadCatalog() {
    const [classResponse] = await Promise.all([
      schoolApi<{ items: ClassOption[]; total: number }>('/api/v1/classes?offset=0&limit=200'),
    ]);
    setClasses(classResponse.items);
  }

  async function loadReport(isRefresh = false) {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    setNotice(null);
    try {
      const query = new URLSearchParams();
      if (filters.class_section_id) query.set("class_section_id", filters.class_section_id);
      if (filters.start_date) query.set("start_date", filters.start_date);
      if (filters.end_date) query.set("end_date", filters.end_date);
      const response = await schoolApi<ReportPayload>(`/api/v1/reports/custom${query.toString() ? `?${query.toString()}` : ""}`);
      setReport(response);
    } catch (error: unknown) {
      setNotice({
        message: error instanceof Error ? error.message : "Unable to generate the report.",
        tone: "error",
      });
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    void (async () => {
      try {
        await loadCatalog();
        await loadReport();
      } catch (error: unknown) {
        setNotice({
          message: error instanceof Error ? error.message : "Unable to load the custom report builder.",
          tone: "error",
        });
        setLoading(false);
      }
    })();
  }, []);

  async function exportReport() {
    if (!report) return;
    setSaving(true);
    try {
      const rows = report.rows.map((row) => ({
        student_name: row.student_name,
        admission_number: row.admission_number,
        class_name: row.class_name,
        attendance_rate: row.attendance_rate,
        average_grade: row.average_grade,
        fees_outstanding: row.fees_outstanding,
        fees_collected: row.fees_collected,
        status: row.status,
      }));
      const blob = new Blob([JSON.stringify({ summary: report.summary, rows }, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "northstar-custom-report.json";
      link.click();
      URL.revokeObjectURL(url);
      setNotice({ message: "Custom report exported successfully.", tone: "success" });
    } catch {
      setNotice({ message: "Unable to export the report right now.", tone: "error" });
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <section className="reports-state">Loading custom reports…</section>;
  }

  return (
    <div className="reports-workspace">
      <div className="reports-toolbar panel">
        <div>
          <div className="eyebrow">
            <span className="sparkle">✳</span> REPORT BUILDER
          </div>
          <h2>Custom insight reports</h2>
        </div>
        <div className="reports-actions">
          <button className="button button-secondary" type="button" onClick={() => void loadReport(true)} disabled={refreshing}>
            <RefreshCw size={14} className={refreshing ? "analytics-refreshing" : ""} />
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
          <button className="button button-primary" type="button" onClick={() => void exportReport()} disabled={!report || saving}>
            <Download size={14} />
            {saving ? "Exporting…" : "Export JSON"}
          </button>
        </div>
      </div>

      <section className="panel reports-filter-panel">
        <div className="reports-filter-header">
          <span><Filter size={15} /> Filters</span>
        </div>
        <div className="reports-filter-grid">
          <label>
            <span>Class</span>
            <select value={filters.class_section_id} onChange={(event) => setFilters((current) => ({ ...current, class_section_id: event.target.value }))}>
              <option value="">All classes</option>
              {classes.map((classItem) => (
                <option key={classItem.id} value={classItem.id}>{classItem.name}</option>
              ))}
            </select>
          </label>
          <label>
            <span>Start date</span>
            <input type="date" value={filters.start_date} onChange={(event) => setFilters((current) => ({ ...current, start_date: event.target.value }))} />
          </label>
          <label>
            <span>End date</span>
            <input type="date" value={filters.end_date} onChange={(event) => setFilters((current) => ({ ...current, end_date: event.target.value }))} />
          </label>
          <div className="reports-filter-actions">
            <button className="button button-primary" type="button" onClick={() => void loadReport(true)}>Generate report</button>
          </div>
        </div>
      </section>

      <section className="reports-stat-grid" aria-label="Report summary">
        {summaryCards.map((card) => (
          <article key={card.label} className={`panel reports-stat-card reports-${card.accent}`}>
            <span>{card.label}</span>
            <strong>{card.value}</strong>
          </article>
        ))}
      </section>

      <section className="panel reports-table-panel">
        <div className="reports-table-header">
          <div>
            <div className="eyebrow"><span className="sparkle">✳</span> STUDENT REPORT</div>
            <h3>Performance overview</h3>
          </div>
          <div className="reports-table-meta">
            <BarChart3 size={15} />
            {report ? `${report.rows.length} records` : "0 records"}
          </div>
        </div>

        {report && report.rows.length > 0 ? (
          <div className="reports-table-wrap">
            <table className="transport-table">
              <thead>
                <tr>
                  <th>Student</th>
                  <th>Class</th>
                  <th>Attendance</th>
                  <th>Average grade</th>
                  <th>Fees collected</th>
                  <th>Outstanding</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {report.rows.map((row) => (
                  <tr key={row.student_id}>
                    <td>
                      <strong>{row.student_name}</strong>
                      <small>{row.admission_number}</small>
                    </td>
                    <td>{row.class_name}</td>
                    <td>{percentileLabel(row.attendance_rate)}</td>
                    <td>{percentileLabel(row.average_grade)}</td>
                    <td>{formatCurrency(row.fees_collected)}</td>
                    <td>{formatCurrency(row.fees_outstanding)}</td>
                    <td>
                      <span className={`report-status report-status-${statusClassName(row.status)}`}>{row.status}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="reports-empty">No students matched the selected report filters.</p>
        )}
      </section>

      {notice && (
        <FeedbackModal
          title={notice.tone === "error" ? "Report notice" : notice.tone === "success" ? "Report generated" : "Update"}
          message={notice.message}
          onClose={() => setNotice(null)}
          tone={notice.tone}
        />
      )}
    </div>
  );
}
