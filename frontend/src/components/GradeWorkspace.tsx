"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { ArrowLeft, BookOpen, ClipboardCheck, FileText, GraduationCap, RotateCw, Save, Wifi, WifiOff } from "lucide-react";
import { schoolApi } from "../lib/school-api";
import FeedbackModal from "./FeedbackModal";
import {
  attendanceTenant,
  cacheAccount,
  cacheClasses,
  cacheOfflineValue,
  getCachedAccount,
  getCachedClasses,
  getLocalGradesForAssessment,
  getLocalGradesForUser,
  getOfflineValue,
  retryFailedGrades,
  saveGradeOffline,
  syncPendingGrades,
  type CachedAccount,
  type ClassSection,
  type LocalGrade,
} from "../lib/offline-attendance";

type AcademicTerm = {
  id: string;
  academic_year_id: string;
  academic_year: string;
  name: string;
  start_date: string;
  end_date: string;
};

type Assessment = {
  id: string;
  term_id: string | null;
  term_name: string | null;
  class_section_id: string;
  class_name: string;
  academic_year: string;
  subject: string;
  title: string;
  assessment_date: string;
  max_score: number | string;
  graded_count: number;
};

type GradebookStudent = {
  student_id: string;
  admission_number: string;
  first_name: string;
  last_name: string;
  score: number | string | null;
  is_currently_enrolled: boolean;
  sync_state?: "pending" | "failed";
  sync_error?: string;
};

type Gradebook = {
  assessment: Assessment;
  students: GradebookStudent[];
};

type Account = {
  id: string;
  email: string;
  role: string;
};

function localDate(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function gradebookWithLocalGrades(
  gradebook: Gradebook,
  localGrades: LocalGrade[],
): Gradebook {
  const pendingByStudent = new Map(
    localGrades.map((grade) => [grade.student_id, grade]),
  );
  return {
    ...gradebook,
    students: gradebook.students.map((student) => {
      const local = pendingByStudent.get(student.student_id);
      return local
        ? {
            ...student,
            score: local.score,
            sync_state: local.sync_state === "synced" ? undefined : local.sync_state,
            sync_error: local.sync_error,
          }
        : student;
    }),
  };
}

export default function GradeWorkspace() {
  const [tenant, setTenant] = useState("");
  const [account, setAccount] = useState<Account | null>(null);
  const [classes, setClasses] = useState<ClassSection[]>([]);
  const [classId, setClassId] = useState("");
  const [terms, setTerms] = useState<AcademicTerm[]>([]);
  const [termId, setTermId] = useState("");
  const [assessmentTermId, setAssessmentTermId] = useState("");
  const [assessments, setAssessments] = useState<Assessment[]>([]);
  const [assessmentId, setAssessmentId] = useState("");
  const [gradebook, setGradebook] = useState<Gradebook | null>(null);
  const [scores, setScores] = useState<Record<string, string>>({});
  const [dirtyStudentIds, setDirtyStudentIds] = useState<Set<string>>(() => new Set());
  const [subject, setSubject] = useState("");
  const [title, setTitle] = useState("");
  const [assessmentDate, setAssessmentDate] = useState(localDate);
  const [maxScore, setMaxScore] = useState("100");
  const [loading, setLoading] = useState(true);
  const [savingAssessment, setSavingAssessment] = useState(false);
  const [savingGrades, setSavingGrades] = useState(false);
  const [online, setOnline] = useState(true);
  const [syncBusy, setSyncBusy] = useState(false);
  const [pendingCount, setPendingCount] = useState(0);
  const [failedCount, setFailedCount] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    async function load() {
      if (!window.sessionStorage.getItem("northstar_access_token")) {
        window.location.replace("/login");
        return;
      }
      let tenantSubdomain: string;
      try {
        tenantSubdomain = attendanceTenant();
        setTenant(tenantSubdomain);
        setOnline(navigator.onLine);
      } catch (tenantError: unknown) {
        setError(tenantError instanceof Error ? tenantError.message : "Open grades on this school's tenant subdomain.");
        setLoading(false);
        return;
      }

      let cachedAccount: CachedAccount | undefined;
      let cachedClasses: ClassSection[] | undefined;
      try {
        [cachedAccount, cachedClasses] = await Promise.all([
          getCachedAccount(tenantSubdomain),
          getCachedClasses(tenantSubdomain),
        ]);
      } catch (storageError: unknown) {
        setError(storageError instanceof Error ? storageError.message : "Browser offline storage is unavailable.");
      }

      try {
        const currentAccount = await schoolApi<Account>("/api/v1/auth/me");
        const classSections = await schoolApi<ClassSection[]>(
          currentAccount.role === "teacher" ? "/api/v1/teacher/classes" : "/api/v1/classes",
        );
        if (!active) return;
        setAccount(currentAccount);
        setClasses(classSections);
        const requestedClass = new URLSearchParams(window.location.search).get("class");
        setClassId(
          classSections.some((item) => item.id === requestedClass)
            ? requestedClass ?? ""
            : classSections[0]?.id ?? "",
        );
        try {
          await Promise.all([
            cacheAccount(tenantSubdomain, currentAccount),
            cacheClasses(tenantSubdomain, classSections),
          ]);
        } catch (storageError: unknown) {
          setError(storageError instanceof Error ? storageError.message : "Unable to cache classes for offline use.");
        }
      } catch (loadError: unknown) {
        if (!active) return;
        if (!window.sessionStorage.getItem("northstar_access_token")) {
          window.location.replace("/login");
          return;
        }
        if (cachedAccount && cachedClasses) {
          setAccount(cachedAccount);
          setClasses(cachedClasses);
          setClassId(cachedClasses[0]?.id ?? "");
          setNotice("Offline mode. Cached classes and gradebooks are available; saved grades will sync when online.");
        } else {
          setError(loadError instanceof Error ? loadError.message : "Unable to load grades.");
        }
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
      if (!currentClass || !tenant) {
        setTerms([]);
        setTermId("");
        setAssessmentTermId("unassigned");
        return;
      }
      setTerms([]);
      setTermId("");
      setAssessmentTermId("");
      try {
        const items = await schoolApi<AcademicTerm[]>(
          `/api/v1/academic-years/${encodeURIComponent(currentClass.academic_year_id)}/terms`,
        );
        if (!active) return;
        setTerms(items);
        const today = localDate();
        const selectedTermId =
          items.find((term) => term.start_date <= today && today <= term.end_date)?.id
            ?? items[0]?.id
            ?? "";
        setTermId(selectedTermId);
        setAssessmentTermId(selectedTermId || "unassigned");
        try {
          await cacheOfflineValue(tenant, `terms:${currentClass.academic_year_id}`, items);
        } catch (storageError: unknown) {
          if (active) setError(storageError instanceof Error ? storageError.message : "Unable to cache academic terms for offline use.");
        }
      } catch (loadError: unknown) {
        if (active) {
          try {
            const cached = await getOfflineValue<AcademicTerm[]>(
              tenant,
              `terms:${currentClass.academic_year_id}`,
            );
            if (cached) {
              setTerms(cached);
              const today = localDate();
              const selectedTerm = cached.find((term) => term.start_date <= today && today <= term.end_date)
                ?? cached[0];
              setTermId(selectedTerm?.id ?? "");
              setAssessmentTermId(selectedTerm?.id ?? "unassigned");
            } else {
              setError(loadError instanceof Error ? loadError.message : "Unable to load academic terms.");
            }
          } catch (storageError: unknown) {
            setError(storageError instanceof Error ? storageError.message : "Unable to read cached academic terms.");
          }
        }
      }
    }
    void loadTerms();
    return () => {
      active = false;
    };
  }, [classId, classes, tenant]);

  useEffect(() => {
    let active = true;
    async function loadAssessments() {
      if (!classId || !assessmentTermId) {
        setAssessments([]);
        setAssessmentId("");
        setGradebook(null);
        return;
      }
      setError("");
      setGradebook(null);
      try {
        const items = await schoolApi<Assessment[]>(
          `/api/v1/classes/${encodeURIComponent(classId)}/assessments?term_id=${encodeURIComponent(assessmentTermId)}`,
        );
        if (!active) return;
        setAssessments(items);
        setAssessmentId((current) =>
          items.some((item) => item.id === current) ? current : items[0]?.id ?? "",
        );
        try {
          await cacheOfflineValue(tenant, `assessments:${classId}:${assessmentTermId}`, items);
        } catch (storageError: unknown) {
          if (active) setError(storageError instanceof Error ? storageError.message : "Unable to cache assessments for offline use.");
        }
      } catch (loadError: unknown) {
        if (active) {
          try {
            const cached = await getOfflineValue<Assessment[]>(
              tenant,
              `assessments:${classId}:${assessmentTermId}`,
            );
            if (cached) {
              setAssessments(cached);
              setAssessmentId((current) =>
                cached.some((item) => item.id === current) ? current : cached[0]?.id ?? "",
              );
            } else {
              setError(loadError instanceof Error ? loadError.message : "Unable to load assessments.");
            }
          } catch (storageError: unknown) {
            setError(storageError instanceof Error ? storageError.message : "Unable to read cached assessments.");
          }
        }
      }
    }
    void loadAssessments();
    return () => {
      active = false;
    };
  }, [assessmentTermId, classId, tenant]);

  const refreshQueueCounts = useCallback(async () => {
    if (!tenant || !account) return;
    const records = await getLocalGradesForUser(tenant, account.id);
    setPendingCount(records.filter((record) => record.sync_state === "pending").length);
    setFailedCount(records.filter((record) => record.sync_state === "failed").length);
  }, [account, tenant]);

  const syncQueue = useCallback(async () => {
    if (!tenant || !account || !navigator.onLine || syncBusy) return;
    setSyncBusy(true);
    try {
      const report = await syncPendingGrades(tenant, account.id);
      await refreshQueueCounts();
      if (report.synced > 0 && gradebook) {
        const remaining = await getLocalGradesForAssessment(
          tenant,
          gradebook.assessment.id,
          account.id,
        );
        const remainingByStudent = new Map(
          remaining.map((record) => [record.student_id, record]),
        );
        setGradebook((current) => current && ({
          ...current,
          students: current.students.map((student) => {
            const record = remainingByStudent.get(student.student_id);
            return {
              ...student,
              sync_state: record?.sync_state === "synced" ? undefined : record?.sync_state,
              sync_error: record?.sync_error,
            };
          }),
        }));
      }
      if (report.last_error) setError(report.last_error);
      else if (report.synced > 0) {
        setNotice(`${report.synced} grade${report.synced === 1 ? "" : "s"} synced.`);
      }
    } catch (syncError: unknown) {
      setError(syncError instanceof Error ? syncError.message : "Unable to sync grades.");
    } finally {
      setSyncBusy(false);
    }
  }, [account, gradebook, refreshQueueCounts, syncBusy, tenant]);

  useEffect(() => {
    if (!account || !tenant) return;
    void refreshQueueCounts().catch((queueError: unknown) => {
      setError(queueError instanceof Error ? queueError.message : "Unable to read the grade sync queue.");
    });
    const updateConnection = () => {
      setOnline(navigator.onLine);
      if (navigator.onLine) void syncQueue();
    };
    window.addEventListener("online", updateConnection);
    window.addEventListener("offline", updateConnection);
    const interval = window.setInterval(() => {
      if (navigator.onLine) void syncQueue();
    }, 30_000);
    return () => {
      window.removeEventListener("online", updateConnection);
      window.removeEventListener("offline", updateConnection);
      window.clearInterval(interval);
    };
  }, [account, refreshQueueCounts, syncQueue, tenant]);

  useEffect(() => {
    let active = true;
    async function loadGradebook() {
      if (!assessmentId) {
        setGradebook(null);
        setScores({});
        setDirtyStudentIds(new Set());
        return;
      }
      setError("");
      try {
        const result = await schoolApi<Gradebook>(
          `/api/v1/assessments/${encodeURIComponent(assessmentId)}/grades`,
        );
        if (!active) return;
        const localGrades = account
          ? await getLocalGradesForAssessment(tenant, assessmentId, account.id)
          : [];
        const merged = gradebookWithLocalGrades(result, localGrades.filter((grade) => grade.sync_state !== "synced"));
        setGradebook(merged);
        setDirtyStudentIds(new Set());
        setScores(Object.fromEntries(
          merged.students.map((student) => [
            student.student_id,
            student.score === null ? "" : String(student.score),
          ]),
        ));
        try {
          await cacheOfflineValue(tenant, `gradebook:${assessmentId}`, result);
        } catch (storageError: unknown) {
          if (active) setError(storageError instanceof Error ? storageError.message : "Unable to cache this gradebook for offline use.");
        }
      } catch (loadError: unknown) {
        if (active) {
          try {
            const cached = await getOfflineValue<Gradebook>(tenant, `gradebook:${assessmentId}`);
            if (cached && account) {
              const localGrades = await getLocalGradesForAssessment(tenant, assessmentId, account.id);
              const merged = gradebookWithLocalGrades(cached, localGrades.filter((grade) => grade.sync_state !== "synced"));
              setGradebook(merged);
              setDirtyStudentIds(new Set());
              setScores(Object.fromEntries(
                merged.students.map((student) => [
                  student.student_id,
                  student.score === null ? "" : String(student.score),
                ]),
              ));
            } else {
              setGradebook(null);
              setError(loadError instanceof Error ? loadError.message : "Unable to load this gradebook.");
            }
          } catch (storageError: unknown) {
            setGradebook(null);
            setError(storageError instanceof Error ? storageError.message : "Unable to read the cached gradebook.");
          }
        }
      }
    }
    void loadGradebook();
    return () => {
      active = false;
    };
  }, [account, assessmentId, tenant]);

  async function createAssessment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!classId || !termId) return;
    setSavingAssessment(true);
    setError("");
    setNotice("");
    try {
      const created = await schoolApi<Assessment>(
        `/api/v1/classes/${encodeURIComponent(classId)}/assessments`,
        {
          method: "POST",
          body: JSON.stringify({
            term_id: termId,
            subject,
            title,
            assessment_date: assessmentDate,
            max_score: Number(maxScore),
          }),
        },
      );
      setAssessments((current) => [created, ...current]);
      setAssessmentId(created.id);
      setSubject("");
      setTitle("");
      setNotice(`${created.subject} · ${created.title} was created.`);
    } catch (createError: unknown) {
      setError(createError instanceof Error ? createError.message : "Assessment creation failed.");
    } finally {
      setSavingAssessment(false);
    }
  }

  async function saveGrades(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!gradebook) return;
    setSavingGrades(true);
    setError("");
    setNotice("");
    try {
      const entries = gradebook.students.filter((student) =>
        dirtyStudentIds.has(student.student_id),
      ).map((student) => {
        const value = scores[student.student_id] ?? "";
        return {
          student_id: student.student_id,
          score: value === "" ? null : Number(value),
        };
      });
      if (!account || !tenant) {
        throw new Error("Grade entries cannot be saved until the school account is loaded.");
      }
      for (const entry of entries) {
        await saveGradeOffline(tenant, account.id, {
          assessment_id: gradebook.assessment.id,
          class_section_id: gradebook.assessment.class_section_id,
          student_id: entry.student_id,
          score: entry.score,
        });
      }
      const localGrades = await getLocalGradesForAssessment(
        tenant,
        gradebook.assessment.id,
        account.id,
      );
      const updated = gradebookWithLocalGrades(
        gradebook,
        localGrades.filter((grade) => grade.sync_state !== "synced"),
      );
      setGradebook(updated);
      setDirtyStudentIds(new Set());
      setAssessments((current) => current.map((item) =>
        item.id === updated.assessment.id
          ? {
              ...item,
              graded_count: updated.students.filter((student) => student.score !== null).length,
            }
          : item,
      ));
      setScores(Object.fromEntries(
        updated.students.map((student) => [
          student.student_id,
          student.score === null ? "" : String(student.score),
        ]),
      ));
      await refreshQueueCounts();
      setGradebook((current) => current && ({
        ...current,
        students: current.students.map((student) =>
          dirtyStudentIds.has(student.student_id)
            ? { ...student, sync_state: "pending", sync_error: undefined }
            : student,
        ),
      }));
      setNotice(`Saved ${entries.length} grade${entries.length === 1 ? "" : "s"} on this device${navigator.onLine ? "; syncing now." : " while offline."}`);
      if (navigator.onLine) void syncQueue();
    } catch (saveError: unknown) {
      setError(saveError instanceof Error ? saveError.message : "Grades could not be saved on this device.");
    } finally {
      setSavingGrades(false);
    }
  }

  async function retryFailed() {
    if (!tenant || !account) return;
    try {
      await retryFailedGrades(tenant, account.id);
      await refreshQueueCounts();
      await syncQueue();
    } catch (retryError: unknown) {
      setError(retryError instanceof Error ? retryError.message : "Unable to retry failed grade sync.");
    }
  }

  if (loading) {
    return <main className="workspace-loading" aria-live="polite">Loading grades…</main>;
  }

  const schoolName = typeof window === "undefined"
    ? ""
    : window.location.hostname.split(".")[0]
      .replace(/-/g, " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());

  return (
    <div className="student-workspace">
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
        <div className="student-side-link active"><BookOpen size={17} /> Grades</div>
        <Link className="student-side-link" href="/report-cards"><FileText size={17} /> Report cards</Link>
        <div className="student-account">
          <span className="student-account-avatar">{account?.email[0]?.toUpperCase() ?? "A"}</span>
          <span><strong>{account?.email}</strong><small>{account?.role === "teacher" ? "Teacher" : "School administrator"}</small></span>
        </div>
      </aside>

      <main className="student-main">
        <header className="student-topbar">
          <div><span>Workspace</span><b>/</b><strong>Grades</strong></div>
          <button className="student-signout" onClick={() => {
            window.sessionStorage.removeItem("northstar_access_token");
            window.location.replace("/login");
          }}>Sign out</button>
        </header>

        <div className="student-page-content">
          <section className="student-page-heading">
            <div>
              <p className="eyebrow">ASSESSMENTS & RESULTS</p>
              <h1>Grades</h1>
              <p className="page-description">Create class assessments and record each student’s score.</p>
            </div>
            <div className="student-page-actions grade-page-actions">
              <span className={`attendance-connection${online ? " online" : ""}`}>
                {online ? <Wifi size={15} /> : <WifiOff size={15} />}
                {online ? "Online" : "Offline"}
              </span>
              <span className="grade-class-caption">{pendingCount} pending · {failedCount} failed</span>
              {failedCount > 0 && <button className="button button-secondary" onClick={() => void retryFailed()} disabled={!online || syncBusy}>Retry failed</button>}
              <button className="button button-secondary" onClick={() => void syncQueue()} disabled={!online || syncBusy || pendingCount === 0}>
                <RotateCw size={15} className={syncBusy ? "attendance-spin" : ""} />
                {syncBusy ? "Syncing…" : "Sync grades"}
              </button>
            </div>
          </section>

          <section className="panel grade-assessment-panel">
            <div className="student-records-toolbar">
              <div><h2>New assessment</h2><p>Set a subject, assessment name, date, and maximum score.</p></div>
            </div>
            <form className="grade-create-form" onSubmit={(event) => void createAssessment(event)}>
              <label>Class section
                <select required value={classId} onChange={(event) => setClassId(event.target.value)} disabled={classes.length === 0}>
                  {classes.length === 0 && <option value="">No class sections available</option>}
                  {classes.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.academic_year}</option>)}
                </select>
              </label>
              <label>Academic term
                <select required value={termId} onChange={(event) => setTermId(event.target.value)} disabled={terms.length === 0}>
                  {terms.length === 0 && <option value="">No terms configured</option>}
                  {terms.map((term) => <option key={term.id} value={term.id}>{term.name} · {term.start_date} – {term.end_date}</option>)}
                </select>
              </label>
              <label>Subject
                <input required maxLength={100} value={subject} onChange={(event) => setSubject(event.target.value)} placeholder="Mathematics" />
              </label>
              <label>Assessment name
                <input required maxLength={120} value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Mid-term test" />
              </label>
              <label>Date
                <input required type="date" value={assessmentDate} onChange={(event) => setAssessmentDate(event.target.value)} />
              </label>
              <label>Maximum score
                <input required type="number" min="0.01" max="999999.99" step="0.01" value={maxScore} onChange={(event) => setMaxScore(event.target.value)} />
              </label>
              <button className="button button-primary" type="submit" disabled={!classId || !termId || savingAssessment}>
                {savingAssessment ? "Creating…" : "Create assessment"}
              </button>
            </form>
          </section>

          <section className="panel gradebook-panel">
            <div className="student-records-toolbar">
              <div><h2>Gradebook</h2><p>Choose an assessment, then enter scores up to its maximum.</p></div>
              <div className="gradebook-filters">
                <label className="grade-assessment-picker">Assessment term
                  <select value={assessmentTermId} onChange={(event) => setAssessmentTermId(event.target.value)}>
                    {terms.map((term) => <option key={term.id} value={term.id}>{term.name}</option>)}
                    <option value="unassigned">Unassigned (pre-term)</option>
                  </select>
                </label>
                <label className="grade-assessment-picker">Assessment
                  <select value={assessmentId} onChange={(event) => setAssessmentId(event.target.value)} disabled={assessments.length === 0}>
                    {assessments.length === 0 && <option value="">No assessments in this selection</option>}
                    {assessments.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.term_name ? `${item.term_name} · ` : "Unassigned · "}{item.subject} · {item.title} · {item.assessment_date}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            </div>
            {gradebook && (
              <>
                <div className="gradebook-summary">
                  <strong>{gradebook.assessment.subject} · {gradebook.assessment.title}</strong>
                  <span>{gradebook.assessment.assessment_date} · out of {gradebook.assessment.max_score}</span>
                  <span>{gradebook.assessment.graded_count} of {gradebook.students.length} graded</span>
                </div>
                <form onSubmit={(event) => void saveGrades(event)}>
                  <div className="table-scroll">
                    <table className="student-record-table gradebook-table">
                      <thead><tr><th>STUDENT</th><th>ADMISSION NO.</th><th>STATUS</th><th>SCORE</th><th>PERCENT</th><th>SYNC STATUS</th></tr></thead>
                      <tbody>
                        {gradebook.students.map((student) => {
                          const score = scores[student.student_id] ?? "";
                          const percent = score === "" || Number(gradebook.assessment.max_score) <= 0
                            ? "—"
                            : `${((Number(score) / Number(gradebook.assessment.max_score)) * 100).toFixed(1)}%`;
                          return (
                            <tr key={student.student_id}>
                              <td><strong className="class-table-name">{student.first_name} {student.last_name}</strong></td>
                              <td>{student.admission_number}</td>
                              <td><span className={`grade-enrollment-state${student.is_currently_enrolled ? " current" : ""}`}>{student.is_currently_enrolled ? "Enrolled" : "Previous enrollment"}</span></td>
                              <td>
                                <input
                                  aria-label={`Score for ${student.first_name} ${student.last_name}`}
                                  type="number"
                                  min="0"
                                  max={gradebook.assessment.max_score}
                                  step="0.01"
                                  value={score}
                                  onChange={(event) => {
                                    setScores((current) => ({
                                      ...current,
                                      [student.student_id]: event.target.value,
                                    }));
                                    setDirtyStudentIds((current) => new Set(current).add(student.student_id));
                                  }}
                                />
                              </td>
                              <td>{percent}</td>
                              <td>
                                {student.sync_state
                                  ? <span className={`attendance-sync-state ${student.sync_state}`} title={student.sync_error}>{student.sync_state === "failed" ? "Needs attention" : "Pending sync"}</span>
                                  : <span className="class-name">Synced</span>}
                              </td>
                            </tr>
                          );
                        })}
                        {gradebook.students.length === 0 && <tr><td colSpan={6} className="empty-row">No active students are enrolled in this class.</td></tr>}
                      </tbody>
                    </table>
                  </div>
                  {gradebook.students.length > 0 && (
                    <footer className="gradebook-footer">
                      <span>{dirtyStudentIds.size} unsaved · clearing a score removes that grade entry.</span>
                      <button className="button button-primary" type="submit" disabled={savingGrades || dirtyStudentIds.size === 0}>
                        <Save size={15} /> {savingGrades ? "Saving…" : "Save grades"}
                      </button>
                    </footer>
                  )}
                </form>
              </>
            )}
            {!gradebook && assessments.length === 0 && (
              <p className="gradebook-empty">
                {terms.length === 0
                  ? <>Configure terms for this academic year in <Link href="/classes">Classes</Link> before adding assessments. Existing unassigned assessments remain available above.</>
                  : "Create an assessment above to start entering student grades."}
              </p>
            )}
          </section>
          <footer className="student-page-footer">Grade entry is restricted to school administrators. Teacher accounts and subject assignments are not yet available.</footer>
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
    </div>
  );
}
