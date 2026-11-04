"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, BookOpen, ClipboardCheck, GraduationCap, Megaphone, MessageCircle } from "lucide-react";
import { schoolApi } from "../lib/school-api";
import FeedbackModal from "./FeedbackModal";

type Account = {
  email: string;
  role: string;
};

type Child = {
  id: string;
  admission_number: string;
  first_name: string;
  last_name: string;
  class_name: string;
  academic_year: string;
};

type AttendanceRecord = {
  attendance_date: string;
  class_name: string;
  status: "present" | "absent" | "late" | "excused";
};

type ReportCard = {
  student_id: string;
  class_name: string;
  academic_year: string;
  term_name: string;
  term_start_date: string;
  term_end_date: string;
  assessments: {
    subject: string;
    title: string;
    assessment_date: string;
    score: number | string;
    max_score: number | string;
    percentage: number | string;
  }[];
  subjects: {
    subject: string;
    score_total: number | string;
    max_score_total: number | string;
    percentage: number | string;
    assessment_count: number;
  }[];
  overall_percentage: number | string | null;
  attendance: {
    present: number;
    late: number;
    absent: number;
    excused: number;
    marked_days: number;
    attendance_rate: number | string | null;
  };
};

type ParentInvoice = {
  id: string;
  invoice_number: string;
  student_id: string;
  student_name: string;
  class_name: string;
  term_name: string;
  amount: number | string;
  currency: string;
  balance: number | string;
  status: "issued" | "paid" | "overdue";
  due_date: string;
  line_items: { description: string; amount: number | string }[];
  payments: { id: string; amount: number | string; method: string; paid_at: string; voided_at: string | null }[];
};

type ParentBillingSettings = {
  provider: "manual" | "paystack" | "stripe";
  currency: string;
  configured: boolean;
};

type ParentAnnouncement = {
  id: string;
  title: string;
  body: string;
  created_at: string;
  published_at: string;
};

function displayPercentage(value: number | string | null): string {
  return value === null ? "—" : `${Number(value).toFixed(1)}%`;
}

function displayMoney(value: number | string, currency: string): string {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(Number(value));
}

export default function ParentPortalWorkspace() {
  const [account, setAccount] = useState<Account | null>(null);
  const [children, setChildren] = useState<Child[]>([]);
  const [selectedChildId, setSelectedChildId] = useState("");
  const [attendance, setAttendance] = useState<AttendanceRecord[]>([]);
  const [reportCards, setReportCards] = useState<ReportCard[]>([]);
  const [invoices, setInvoices] = useState<ParentInvoice[]>([]);
  const [billingSettings, setBillingSettings] = useState<ParentBillingSettings | null>(null);
  const [announcements, setAnnouncements] = useState<ParentAnnouncement[]>([]);
  const [loading, setLoading] = useState(true);
  const [contentLoading, setContentLoading] = useState(false);
  const [checkoutInvoiceId, setCheckoutInvoiceId] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    async function loadPortal() {
      if (!window.sessionStorage.getItem("northstar_access_token")) {
        window.location.replace("/login");
        return;
      }
      try {
        const currentAccount = await schoolApi<Account>("/api/v1/auth/me");
        if (currentAccount.role !== "guardian") {
          window.location.replace(currentAccount.role === "teacher" ? "/attendance" : "/students");
          return;
        }
        const [linkedChildren, invoiceList, paymentSettings] = await Promise.all([
          schoolApi<Child[]>("/api/v1/parents/children"),
          schoolApi<{ items: ParentInvoice[]; total: number }>("/api/v1/parents/invoices"),
          schoolApi<ParentBillingSettings>("/api/v1/parents/billing/settings"),
        ]);
        if (!active) return;
        setAccount(currentAccount);
        setChildren(linkedChildren);
        setInvoices(invoiceList.items);
        setBillingSettings(paymentSettings);
        setSelectedChildId(linkedChildren[0]?.id ?? "");
      } catch (loadError: unknown) {
        if (!active) return;
        if (!window.sessionStorage.getItem("northstar_access_token")) {
          window.location.replace("/login");
          return;
        }
        setError(loadError instanceof Error ? loadError.message : "Unable to load the parent portal.");
      } finally {
        if (active) setLoading(false);
      }
    }
    void loadPortal();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!account) return;
    let active = true;
    schoolApi<{ items: ParentAnnouncement[]; total: number }>("/api/v1/parents/announcements?limit=100")
      .then((result) => {
        if (active) setAnnouncements(result.items);
      })
      .catch((loadError: unknown) => {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : "Unable to load school announcements.");
        }
      });
    return () => {
      active = false;
    };
  }, [account]);

  useEffect(() => {
    if (!selectedChildId) {
      setAttendance([]);
      setReportCards([]);
      return;
    }
    let active = true;
    setContentLoading(true);
    setError("");
    Promise.all([
      schoolApi<AttendanceRecord[]>(
        `/api/v1/parents/children/${encodeURIComponent(selectedChildId)}/attendance?limit=100`,
      ),
      schoolApi<ReportCard[]>(
        `/api/v1/parents/children/${encodeURIComponent(selectedChildId)}/report-cards`,
      ),
    ])
      .then(([attendanceRecords, cards]) => {
        if (!active) return;
        setAttendance(attendanceRecords);
        setReportCards(cards);
      })
      .catch((loadError: unknown) => {
        if (!active) return;
        setError(loadError instanceof Error ? loadError.message : "Unable to load student progress.");
      })
      .finally(() => {
        if (active) setContentLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selectedChildId]);

  const schoolName = useMemo(
    () => typeof window === "undefined"
      ? ""
      : window.location.hostname.split(".")[0]
        .replace(/-/g, " ")
        .replace(/\b\w/g, (letter) => letter.toUpperCase()),
    [],
  );
  const selectedChild = children.find((child) => child.id === selectedChildId);
  const selectedChildInvoices = invoices.filter((invoice) => invoice.student_id === selectedChildId);
  const attendanceCounts = attendance.reduce(
    (counts, record) => ({ ...counts, [record.status]: counts[record.status] + 1 }),
    { present: 0, absent: 0, late: 0, excused: 0 },
  );

  async function startCheckout(invoiceId: string) {
    setCheckoutInvoiceId(invoiceId);
    try {
      const checkout = await schoolApi<{ checkout_url: string }>(
        `/api/v1/parents/invoices/${encodeURIComponent(invoiceId)}/checkout`,
        { method: "POST" },
      );
      window.location.assign(checkout.checkout_url);
    } catch (checkoutError: unknown) {
      setError(checkoutError instanceof Error ? checkoutError.message : "Unable to start online checkout.");
      setCheckoutInvoiceId("");
    }
  }

  if (loading) {
    return <main className="workspace-loading" aria-live="polite">Loading parent portal…</main>;
  }

  return (
    <div className="student-workspace parent-portal-workspace">
      <aside className="student-sidebar">
        <Link className="brand student-brand" href="/parent">
          <span className="brand-mark"><GraduationCap size={21} /></span>
          <span><span className="brand-name">northstar</span><span className="brand-caption">PARENT PORTAL</span></span>
        </Link>
        <div className="student-school-name">{schoolName || "School workspace"}</div>
        <div className="nav-label">YOUR FAMILY</div>
        <div className="student-side-link active"><BookOpen size={17} /> Student progress</div>
        <Link className="student-side-link" href="/messages"><MessageCircle size={17} /> Messages</Link>
        <div className="student-account">
          <span className="student-account-avatar">{account?.email[0]?.toUpperCase() ?? "P"}</span>
          <span><strong>{account?.email}</strong><small>Parent / guardian</small></span>
        </div>
      </aside>

      <main className="student-main">
        <header className="student-topbar">
          <div><span>Parent portal</span><b>/</b><strong>Student progress</strong></div>
          <button
            className="student-signout"
            onClick={() => {
              window.sessionStorage.removeItem("northstar_access_token");
              window.location.replace("/login");
            }}
          >
            Sign out
          </button>
        </header>

        <div className="student-page-content">
          <section className="student-page-heading">
            <div>
              <p className="eyebrow">FAMILY OVERVIEW</p>
              <h1>Student progress</h1>
              <p className="page-description">View attendance and term results for your linked students.</p>
            </div>
            {children.length > 1 && (
              <label className="parent-child-select">
                Student
                <select value={selectedChildId} onChange={(event) => setSelectedChildId(event.target.value)}>
                  {children.map((child) => (
                    <option key={child.id} value={child.id}>
                      {child.first_name} {child.last_name}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </section>

          <section className="panel parent-section parent-announcements">
            <header className="parent-section-heading">
              <div><Megaphone size={18} /><h2>School announcements</h2></div>
              <span>{announcements.length} updates</span>
            </header>
            {announcements.length === 0 ? (
              <p className="parent-no-records">There are no school announcements at this time.</p>
            ) : (
              <div className="parent-announcement-list">
                {announcements.map((announcement) => (
                  <article className="parent-announcement" key={announcement.id}>
                    <div className="parent-announcement-heading">
                      <h3>{announcement.title}</h3>
                      <time dateTime={announcement.published_at}>
                        {new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(
                          new Date(announcement.published_at),
                        )}
                      </time>
                    </div>
                    <p>{announcement.body}</p>
                  </article>
                ))}
              </div>
            )}
          </section>

          {!selectedChild ? (
            <section className="panel parent-empty-state">
              <GraduationCap size={28} />
              <h2>No linked students yet</h2>
              <p>Ask your school to confirm your email on the student record and send a parent portal invitation.</p>
              <Link className="button button-secondary" href="/login"><ArrowLeft size={14} /> Back to sign in</Link>
            </section>
          ) : (
            <>
              <section className="panel parent-child-card">
                <span className="parent-child-avatar">{selectedChild.first_name[0]?.toUpperCase()}</span>
                <div>
                  <p className="eyebrow">STUDENT</p>
                  <h2>{selectedChild.first_name} {selectedChild.last_name}</h2>
                  <p>{selectedChild.admission_number} · {selectedChild.class_name} · {selectedChild.academic_year}</p>
                </div>
              </section>

              <section className="student-metrics parent-metrics" aria-label="Progress summary">
                <article><span>Present</span><strong>{attendanceCounts.present}</strong></article>
                <article><span>Late</span><strong>{attendanceCounts.late}</strong></article>
                <article><span>Absent</span><strong>{attendanceCounts.absent}</strong></article>
                <article><span>Term reports</span><strong>{reportCards.length}</strong></article>
              </section>

              {contentLoading ? (
                <p className="parent-loading" aria-live="polite">Loading attendance and report cards…</p>
              ) : (
                <>
                  <section className="panel parent-section">
                    <header className="parent-section-heading">
                      <div><ClipboardCheck size={18} /><h2>Attendance history</h2></div>
                      <span>{attendance.length} recent records</span>
                    </header>
                    {attendance.length === 0 ? (
                      <p className="parent-no-records">No attendance records are available yet.</p>
                    ) : (
                      <div className="parent-table-wrap">
                        <table className="parent-table">
                          <thead><tr><th>Date</th><th>Class</th><th>Status</th></tr></thead>
                          <tbody>
                            {attendance.map((record) => (
                              <tr key={`${record.attendance_date}-${record.class_name}`}>
                                <td>{record.attendance_date}</td>
                                <td>{record.class_name}</td>
                                <td><span className={`parent-attendance-status status-${record.status}`}>{record.status}</span></td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </section>

                  <section className="parent-report-section">
                    <header className="parent-section-heading">
                      <div><BookOpen size={18} /><h2>Term report cards</h2></div>
                      <span>{reportCards.length} available</span>
                    </header>
                    {reportCards.length === 0 ? (
                      <section className="panel parent-no-records">Report cards will appear here when term results are available.</section>
                    ) : reportCards.map((card) => (
                      <article className="panel parent-report-card" key={`${card.student_id}-${card.term_name}-${card.term_start_date}`}>
                        <header>
                          <div>
                            <p className="eyebrow">{card.academic_year} · {card.class_name}</p>
                            <h3>{card.term_name}</h3>
                            <span>{card.term_start_date} to {card.term_end_date}</span>
                          </div>
                          <div className="parent-overall-score">
                            <span>Overall</span>
                            <strong>{displayPercentage(card.overall_percentage)}</strong>
                          </div>
                        </header>
                        {card.subjects.length > 0 && (
                          <div className="parent-table-wrap">
                            <table className="parent-table">
                              <thead><tr><th>Subject</th><th>Assessments</th><th>Score</th><th>Result</th></tr></thead>
                              <tbody>
                                {card.subjects.map((subject) => (
                                  <tr key={subject.subject}>
                                    <td>{subject.subject}</td>
                                    <td>{subject.assessment_count}</td>
                                    <td>{Number(subject.score_total).toFixed(1)} / {Number(subject.max_score_total).toFixed(1)}</td>
                                    <td>{displayPercentage(subject.percentage)}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}
                        {card.assessments.length > 0 && (
                          <div className="parent-assessment-results">
                            <h4>Assessment results</h4>
                            <div className="parent-table-wrap">
                              <table className="parent-table">
                                <thead><tr><th>Assessment</th><th>Date</th><th>Score</th><th>Result</th></tr></thead>
                                <tbody>
                                  {card.assessments.map((assessment) => (
                                    <tr key={`${assessment.subject}-${assessment.title}-${assessment.assessment_date}`}>
                                      <td>{assessment.subject} · {assessment.title}</td>
                                      <td>{assessment.assessment_date}</td>
                                      <td>{Number(assessment.score).toFixed(1)} / {Number(assessment.max_score).toFixed(1)}</td>
                                      <td>{displayPercentage(assessment.percentage)}</td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          </div>
                        )}
                        <p className="parent-report-attendance">
                          Attendance this term: {displayPercentage(card.attendance.attendance_rate)}
                          {" · "}{card.attendance.present} present
                          {" · "}{card.attendance.late} late
                          {" · "}{card.attendance.absent} absent
                        </p>
                      </article>
                    ))}
                  </section>

                  <section className="parent-report-section">
                    <header className="parent-section-heading">
                      <div><BookOpen size={18} /><h2>Fees &amp; payments</h2></div>
                      <span>{selectedChildInvoices.length} invoice(s)</span>
                    </header>
                    {selectedChildInvoices.length === 0 ? (
                      <section className="panel parent-no-records">No fee invoices are available for this student yet.</section>
                    ) : selectedChildInvoices.map((invoice) => (
                      <article className="panel parent-report-card parent-invoice-card" key={invoice.id}>
                        <header>
                          <div>
                            <p className="eyebrow">{invoice.invoice_number} · {invoice.class_name}</p>
                            <h3>{invoice.term_name}</h3>
                            <span>Due {invoice.due_date}</span>
                          </div>
                          <div className={`billing-status billing-status-${invoice.status}`}>{invoice.status}</div>
                        </header>
                        <div className="parent-table-wrap">
                          <table className="parent-table">
                            <thead><tr><th>Fee</th><th>Amount</th></tr></thead>
                            <tbody>
                              {invoice.line_items.map((item, index) => (
                                <tr key={`${item.description}-${index}`}>
                                  <td>{item.description}</td>
                                  <td>{displayMoney(item.amount, invoice.currency)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                        <div className="parent-invoice-total">
                          <span>Total {displayMoney(invoice.amount, invoice.currency)}</span>
                          <div>
                            <strong>Balance {displayMoney(invoice.balance, invoice.currency)}</strong>
                            {Number(invoice.balance) > 0 && billingSettings?.provider !== "manual" && billingSettings?.configured && (
                              <button
                                className="button button-primary parent-pay-invoice"
                                type="button"
                                disabled={checkoutInvoiceId === invoice.id}
                                onClick={() => void startCheckout(invoice.id)}
                              >
                                {checkoutInvoiceId === invoice.id ? "Connecting…" : "Pay online"}
                              </button>
                            )}
                          </div>
                        </div>
                        {invoice.payments.length > 0 && (
                          <p className="parent-report-attendance">
                            Payment history: {invoice.payments.map((payment) =>
                              `${displayMoney(payment.amount, invoice.currency)} (${payment.method}${payment.voided_at ? ", voided" : ""})`,
                            ).join(" · ")}
                          </p>
                        )}
                      </article>
                    ))}
                  </section>
                </>
              )}
            </>
          )}
        </div>
      </main>
      <FeedbackModal message={error} tone="error" onClose={() => setError("")} />
    </div>
  );
}
