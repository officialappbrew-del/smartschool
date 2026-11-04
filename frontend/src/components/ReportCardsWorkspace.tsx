"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowLeft, BookOpen, ClipboardCheck, GraduationCap, Printer } from "lucide-react";
import { schoolApi } from "../lib/school-api";
import FeedbackModal from "./FeedbackModal";

type ClassSection = {
  id: string;
  name: string;
  academic_year_id: string;
  academic_year: string;
};

type AcademicTerm = {
  id: string;
  academic_year_id: string;
  academic_year: string;
  name: string;
  start_date: string;
  end_date: string;
};

type ReportAssessment = {
  subject: string;
  title: string;
  assessment_date: string;
  score: number | string;
  max_score: number | string;
  percentage: number | string;
};

type ReportSubject = {
  subject: string;
  score_total: number | string;
  max_score_total: number | string;
  percentage: number | string;
  assessment_count: number;
};

type StudentReportCard = {
  student_id: string;
  admission_number: string;
  first_name: string;
  last_name: string;
  class_name: string;
  academic_year: string;
  term_name: string;
  term_start_date: string;
  term_end_date: string;
  assessments: ReportAssessment[];
  subjects: ReportSubject[];
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

type Account = {
  email: string;
  role: string;
};

export default function ReportCardsWorkspace() {
  const [account, setAccount] = useState<Account | null>(null);
  const [classes, setClasses] = useState<ClassSection[]>([]);
  const [classId, setClassId] = useState("");
  const [terms, setTerms] = useState<AcademicTerm[]>([]);
  const [termId, setTermId] = useState("");
  const [reportCards, setReportCards] = useState<StudentReportCard[]>([]);
  const [studentId, setStudentId] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    async function load() {
      if (!window.sessionStorage.getItem("northstar_access_token")) {
        window.location.replace("/login");
        return;
      }
      try {
        const currentAccount = await schoolApi<Account>("/api/v1/auth/me");
        const classSections = await schoolApi<ClassSection[]>(
          currentAccount.role === "teacher"
            ? "/api/v1/teacher/classes"
            : "/api/v1/classes",
        );
        if (!active) return;
        setAccount(currentAccount);
        setClasses(classSections);
        setClassId(classSections[0]?.id ?? "");
      } catch (loadError: unknown) {
        if (!active) return;
        if (!window.sessionStorage.getItem("northstar_access_token")) {
          window.location.replace("/login");
          return;
        }
        setError(loadError instanceof Error ? loadError.message : "Unable to load report cards.");
      } finally {
        if (active) setLoading(false);
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    async function loadTerms() {
      const currentClass = classes.find((item) => item.id === classId);
      setTerms([]);
      setTermId("");
      setReportCards([]);
      if (!currentClass) return;
      try {
        const items = await schoolApi<AcademicTerm[]>(
          `/api/v1/academic-years/${encodeURIComponent(currentClass.academic_year_id)}/terms`,
        );
        if (!active) return;
        setTerms(items);
        setTermId(items[0]?.id ?? "");
      } catch (loadError: unknown) {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : "Unable to load academic terms.");
        }
      }
    }
    void loadTerms();
    return () => {
      active = false;
    };
  }, [classId, classes]);

  useEffect(() => {
    let active = true;
    async function loadReportCards() {
      if (!classId || !termId) {
        setReportCards([]);
        setStudentId("");
        return;
      }
      setError("");
      try {
        const items = await schoolApi<StudentReportCard[]>(
          `/api/v1/classes/${encodeURIComponent(classId)}/terms/${encodeURIComponent(termId)}/report-cards`,
        );
        if (!active) return;
        setReportCards(items);
        setStudentId((current) =>
          items.some((item) => item.student_id === current)
            ? current
            : items[0]?.student_id ?? "",
        );
      } catch (loadError: unknown) {
        if (active) {
          setReportCards([]);
          setError(loadError instanceof Error ? loadError.message : "Unable to load term report cards.");
        }
      }
    }
    void loadReportCards();
    return () => {
      active = false;
    };
  }, [classId, termId]);

  if (loading) {
    return <main className="workspace-loading" aria-live="polite">Loading report cards…</main>;
  }

  const schoolName = typeof window === "undefined"
    ? ""
    : window.location.hostname.split(".")[0]
      .replace(/-/g, " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());
  const selectedCard = reportCards.find((card) => card.student_id === studentId) ?? null;

  return (
    <div className="student-workspace report-cards-workspace">
      <aside className="student-sidebar">
        <Link className="brand student-brand" href="/">
          <span className="brand-mark"><GraduationCap size={21} /></span>
          <span><span className="brand-name">northstar</span><span className="brand-caption">SCHOOL MANAGEMENT</span></span>
        </Link>
        <div className="student-school-name">{schoolName || "School workspace"}</div>
        <div className="nav-label">WORKSPACE</div>
        <Link className="student-side-link" href="/"><ArrowLeft size={17} /> Overview</Link>
        {account?.role !== "teacher" && <Link className="student-side-link" href="/students"><GraduationCap size={17} /> Students</Link>}
        {account?.role !== "teacher" && <Link className="student-side-link" href="/classes"><GraduationCap size={17} /> Classes</Link>}
        <Link className="student-side-link" href="/attendance"><ClipboardCheck size={17} /> Attendance</Link>
        <Link className="student-side-link" href="/grades"><BookOpen size={17} /> Grades</Link>
        <div className="student-side-link active"><BookOpen size={17} /> Report cards</div>
        <div className="student-account">
          <span className="student-account-avatar">{account?.email[0]?.toUpperCase() ?? "A"}</span>
          <span><strong>{account?.email}</strong><small>{account?.role === "teacher" ? "Teacher" : "School administrator"}</small></span>
        </div>
      </aside>

      <main className="student-main">
        <header className="student-topbar report-toolbar">
          <div><span>Workspace</span><b>/</b><strong>Report cards</strong></div>
          <button className="student-signout no-print" onClick={() => window.print()}>
            <Printer size={15} /> Print report card
          </button>
        </header>
        <div className="student-page-content">
          <section className="student-page-heading no-print">
            <div>
              <p className="eyebrow">TERM RESULTS</p>
              <h1>Student report cards</h1>
              <p className="page-description">Review term assessment results and attendance for a class.</p>
            </div>
          </section>

          <section className="panel report-filter-panel no-print">
            <label>Class section
              <select value={classId} onChange={(event) => setClassId(event.target.value)} disabled={classes.length === 0}>
                {classes.length === 0 && <option value="">No assigned classes</option>}
                {classes.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.academic_year}</option>)}
              </select>
            </label>
            <label>Academic term
              <select value={termId} onChange={(event) => setTermId(event.target.value)} disabled={terms.length === 0}>
                {terms.length === 0 && <option value="">No terms configured</option>}
                {terms.map((term) => <option key={term.id} value={term.id}>{term.name} · {term.start_date} – {term.end_date}</option>)}
              </select>
            </label>
            <label>Student
              <select value={studentId} onChange={(event) => setStudentId(event.target.value)} disabled={reportCards.length === 0}>
                {reportCards.length === 0 && <option value="">No report cards available</option>}
                {reportCards.map((card) => (
                  <option key={card.student_id} value={card.student_id}>
                    {card.last_name}, {card.first_name} · {card.admission_number}
                  </option>
                ))}
              </select>
            </label>
          </section>
          {terms.length === 0 && classes.length > 0 && !error && (
            <p className="attendance-notice no-print">
              No terms are configured for this academic year. Add terms in <Link href="/classes">Classes</Link>.
            </p>
          )}
          {terms.length > 0 && reportCards.length === 0 && !error && (
            <p className="attendance-notice no-print">No enrolled students or recorded grades are available for this class and term.</p>
          )}

          {selectedCard && (
            <article className="panel report-card">
              <header className="report-card-heading">
                <p className="eyebrow">STUDENT REPORT</p>
                <h1>{schoolName || "School workspace"}</h1>
                <p>{selectedCard.academic_year} · {selectedCard.term_name} · {selectedCard.term_start_date} to {selectedCard.term_end_date}</p>
              </header>
              <section className="report-student-details">
                <div><span>Student</span><strong>{selectedCard.first_name} {selectedCard.last_name}</strong></div>
                <div><span>Admission number</span><strong>{selectedCard.admission_number}</strong></div>
                <div><span>Class</span><strong>{selectedCard.class_name}</strong></div>
              </section>
              <section className="report-metrics" aria-label="Term summary">
                <div><span>Overall average</span><strong>{selectedCard.overall_percentage === null ? "—" : `${Number(selectedCard.overall_percentage).toFixed(1)}%`}</strong></div>
                <div><span>Attendance rate</span><strong>{selectedCard.attendance.attendance_rate === null ? "—" : `${Number(selectedCard.attendance.attendance_rate).toFixed(1)}%`}</strong></div>
                <div><span>Marked days</span><strong>{selectedCard.attendance.marked_days}</strong></div>
              </section>
              <section className="report-section">
                <h2>Subject results</h2>
                <div className="table-scroll">
                  <table className="student-record-table report-table">
                    <thead><tr><th>SUBJECT</th><th>SCORE</th><th>MAXIMUM</th><th>AVERAGE</th><th>ASSESSMENTS</th></tr></thead>
                    <tbody>
                      {selectedCard.subjects.map((subject) => (
                        <tr key={subject.subject}>
                          <td><strong className="class-table-name">{subject.subject}</strong></td>
                          <td>{subject.score_total}</td>
                          <td>{subject.max_score_total}</td>
                          <td>{Number(subject.percentage).toFixed(1)}%</td>
                          <td>{subject.assessment_count}</td>
                        </tr>
                      ))}
                      {selectedCard.subjects.length === 0 && <tr><td colSpan={5} className="empty-row">No grades have been recorded for this term.</td></tr>}
                    </tbody>
                  </table>
                </div>
              </section>
              <section className="report-section">
                <h2>Assessment results</h2>
                <div className="table-scroll">
                  <table className="student-record-table report-table">
                    <thead><tr><th>DATE</th><th>SUBJECT</th><th>ASSESSMENT</th><th>SCORE</th><th>MAXIMUM</th><th>PERCENT</th></tr></thead>
                    <tbody>
                      {selectedCard.assessments.map((assessment) => (
                        <tr key={`${assessment.subject}-${assessment.title}-${assessment.assessment_date}`}>
                          <td>{assessment.assessment_date}</td>
                          <td>{assessment.subject}</td>
                          <td>{assessment.title}</td>
                          <td>{assessment.score}</td>
                          <td>{assessment.max_score}</td>
                          <td>{Number(assessment.percentage).toFixed(1)}%</td>
                        </tr>
                      ))}
                      {selectedCard.assessments.length === 0 && <tr><td colSpan={6} className="empty-row">No graded assessments for this term.</td></tr>}
                    </tbody>
                  </table>
                </div>
              </section>
              <section className="report-section">
                <h2>Attendance</h2>
                <div className="report-attendance">
                  <span><strong>{selectedCard.attendance.present}</strong> Present</span>
                  <span><strong>{selectedCard.attendance.late}</strong> Late</span>
                  <span><strong>{selectedCard.attendance.absent}</strong> Absent</span>
                  <span><strong>{selectedCard.attendance.excused}</strong> Excused</span>
                </div>
                <p className="report-footnote">Attendance rate counts present and late marks over present, late, and absent school days. Excused days are excluded.</p>
              </section>
            </article>
          )}
        </div>
      </main>
      <FeedbackModal message={error} tone="error" onClose={() => setError("")} />
    </div>
  );
}
