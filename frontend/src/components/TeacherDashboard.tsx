"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  ArrowRight,
  BookOpen,
  ClipboardCheck,
  GraduationCap,
  MessageCircle,
  RotateCw,
  Wifi,
  WifiOff,
} from "lucide-react";
import FeedbackModal from "./FeedbackModal";
import { schoolApi } from "../lib/school-api";
import {
  attendanceTenant,
  cacheAccount,
  cacheClasses,
  getCachedAccount,
  getCachedClasses,
  getLocalAttendanceForUser,
  getLocalGradesForUser,
  retryFailedAttendance,
  retryFailedGrades,
  syncPendingAttendance,
  syncPendingGrades,
  type CachedAccount,
  type ClassSection,
} from "../lib/offline-attendance";

type TimetableEntry = {
  id: string;
  class_section_id: string;
  class_name: string;
  teacher_id: string;
  teacher_email: string;
  subject: string;
  room: string | null;
  weekday: number;
  start_time: string;
  end_time: string;
};

const weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export default function TeacherDashboard() {
  const [tenant, setTenant] = useState("");
  const [account, setAccount] = useState<CachedAccount | null>(null);
  const [classes, setClasses] = useState<ClassSection[]>([]);
  const [timetable, setTimetable] = useState<TimetableEntry[]>([]);
  const [online, setOnline] = useState(true);
  const [loading, setLoading] = useState(true);
  const [syncBusy, setSyncBusy] = useState(false);
  const [attendancePending, setAttendancePending] = useState(0);
  const [gradesPending, setGradesPending] = useState(0);
  const [failedCount, setFailedCount] = useState(0);
  const [error, setError] = useState("");

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
        if (active) {
          setTenant(tenantSubdomain);
          setOnline(navigator.onLine);
        }
      } catch (tenantError: unknown) {
        if (active) {
          setError(tenantError instanceof Error ? tenantError.message : "Open the teacher dashboard on your school's subdomain.");
          setLoading(false);
        }
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
        if (active) setError(storageError instanceof Error ? storageError.message : "Browser offline storage is unavailable.");
      }

      try {
        const currentAccount = await schoolApi<CachedAccount>("/api/v1/auth/me");
        if (currentAccount.role !== "teacher") {
          window.location.replace("/students");
          return;
        }
        const [teacherClasses, teacherTimetable] = await Promise.all([
          schoolApi<ClassSection[]>("/api/v1/teacher/classes"),
          schoolApi<TimetableEntry[]>("/api/v1/teacher/timetable").catch((timetableError: unknown) => {
            if (active) {
              setError(timetableError instanceof Error ? timetableError.message : "Unable to load your timetable.");
            }
            return [];
          }),
        ]);
        if (!active) return;
        await Promise.all([
          cacheAccount(tenantSubdomain, currentAccount),
          cacheClasses(tenantSubdomain, teacherClasses),
        ]);
        setAccount(currentAccount);
        setClasses(teacherClasses);
        setTimetable(teacherTimetable);
      } catch (loadError: unknown) {
        if (!active) return;
        if (!window.sessionStorage.getItem("northstar_access_token")) {
          window.location.replace("/login");
          return;
        }
        if (cachedAccount?.role === "teacher" && cachedClasses) {
          setAccount(cachedAccount);
          setClasses(cachedClasses);
        } else {
          setError(loadError instanceof Error ? loadError.message : "Unable to load your assigned classes.");
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

  const refreshQueue = useCallback(async () => {
    if (!tenant || !account) return;
    const [attendance, grades] = await Promise.all([
      getLocalAttendanceForUser(tenant, account.id),
      getLocalGradesForUser(tenant, account.id),
    ]);
    setAttendancePending(attendance.filter((record) => record.sync_state === "pending").length);
    setGradesPending(grades.filter((record) => record.sync_state === "pending").length);
    setFailedCount(
      attendance.filter((record) => record.sync_state === "failed").length
      + grades.filter((record) => record.sync_state === "failed").length,
    );
  }, [account, tenant]);

  const syncQueue = useCallback(async () => {
    if (!tenant || !account || !navigator.onLine || syncBusy) return;
    setSyncBusy(true);
    try {
      const [attendanceReport, gradeReport] = await Promise.all([
        syncPendingAttendance(tenant, account.id),
        syncPendingGrades(tenant, account.id),
      ]);
      await refreshQueue();
      const syncError = attendanceReport.last_error ?? gradeReport.last_error;
      if (syncError) setError(syncError);
    } catch (syncError: unknown) {
      setError(syncError instanceof Error ? syncError.message : "Unable to sync offline work.");
    } finally {
      setSyncBusy(false);
    }
  }, [account, refreshQueue, syncBusy, tenant]);

  async function retryFailed() {
    if (!tenant || !account || !navigator.onLine || syncBusy) return;
    setSyncBusy(true);
    try {
      await Promise.all([
        retryFailedAttendance(tenant, account.id),
        retryFailedGrades(tenant, account.id),
      ]);
      const [attendanceReport, gradeReport] = await Promise.all([
        syncPendingAttendance(tenant, account.id),
        syncPendingGrades(tenant, account.id),
      ]);
      await refreshQueue();
      const syncError = attendanceReport.last_error ?? gradeReport.last_error;
      if (syncError) setError(syncError);
    } catch (retryError: unknown) {
      setError(retryError instanceof Error ? retryError.message : "Unable to retry offline work.");
    } finally {
      setSyncBusy(false);
    }
  }

  useEffect(() => {
    if (!account || !tenant) return;
    void refreshQueue().catch((queueError: unknown) => {
      setError(queueError instanceof Error ? queueError.message : "Unable to read the offline queue.");
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
  }, [account, refreshQueue, syncQueue, tenant]);

  if (loading) {
    return <main className="workspace-loading" aria-live="polite">Loading teacher dashboard…</main>;
  }

  const schoolName = tenant.replace(/-/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
  const dateLabel = new Intl.DateTimeFormat(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  }).format(new Date());

  return (
    <div className="student-workspace teacher-dashboard">
      <aside className="student-sidebar">
        <Link className="brand student-brand" href="/">
          <span className="brand-mark"><GraduationCap size={21} /></span>
          <span><span className="brand-name">northstar</span><span className="brand-caption">TEACHER WORKSPACE</span></span>
        </Link>
        <div className="student-school-name">{schoolName || "School workspace"}</div>
        <div className="nav-label">WORKSPACE</div>
        <div className="student-side-link active"><GraduationCap size={17} /> My classes</div>
        <Link className="student-side-link" href="/attendance"><ClipboardCheck size={17} /> Attendance</Link>
        <Link className="student-side-link" href="/grades"><BookOpen size={17} /> Grades</Link>
        <Link className="student-side-link" href="/report-cards"><BookOpen size={17} /> Report cards</Link>
        <Link className="student-side-link" href="/messages"><MessageCircle size={17} /> Messages</Link>
        <div className="student-account">
          <span className="student-account-avatar">{account?.email[0]?.toUpperCase() ?? "T"}</span>
          <span><strong>{account?.email}</strong><small>Teacher</small></span>
        </div>
      </aside>

      <main className="student-main">
        <header className="student-topbar">
          <div><span>Workspace</span><b>/</b><strong>My classes</strong></div>
          <div className={`attendance-connection${online ? " online" : ""}`}>
            {online ? <Wifi size={15} /> : <WifiOff size={15} />}
            {online ? "Online" : "Offline"}
          </div>
        </header>
        <div className="student-page-content">
          <section className="student-page-heading teacher-dashboard-heading">
            <div>
              <p className="eyebrow">TEACHER WORKSPACE</p>
              <h1>Good day{account?.email ? `, ${account.email.split("@")[0]}` : ""}</h1>
              <p className="page-description">{dateLabel} · Your assigned classes and offline work.</p>
            </div>
            <div className="student-page-actions">
              <span className="teacher-sync-summary">{attendancePending + gradesPending} pending · {failedCount} failed</span>
              {failedCount > 0 && <button className="button button-secondary" onClick={() => void retryFailed()} disabled={!online || syncBusy}>Retry failed</button>}
              <button className="button button-secondary" onClick={() => void syncQueue()} disabled={!online || syncBusy || attendancePending + gradesPending === 0}>
                <RotateCw size={15} className={syncBusy ? "attendance-spin" : ""} />
                {syncBusy ? "Syncing…" : "Sync now"}
              </button>
            </div>
          </section>

          <section className="teacher-dashboard-metrics" aria-label="Teacher summary">
            <article className="panel"><span>Assigned classes</span><strong>{classes.length}</strong><small>Available for today</small></article>
            <article className="panel"><span>Attendance to sync</span><strong>{attendancePending}</strong><small>Saved on this device</small></article>
            <article className="panel"><span>Grades to sync</span><strong>{gradesPending}</strong><small>Saved on this device</small></article>
          </section>

          <section className="panel teacher-timetable-panel">
            <header className="teacher-classes-heading">
              <div><h2>My timetable</h2><p>Your scheduled lessons for the week.</p></div>
              <span>{timetable.length} lessons</span>
            </header>
            {timetable.length === 0 ? (
              <p className="teacher-dashboard-hint">No timetable entries are available yet. Ask your school administrator to add your weekly lessons.</p>
            ) : (
              <div className="teacher-schedule-list">
                {timetable.map((lesson) => (
                  <article className="teacher-schedule-item" key={lesson.id}>
                    <span className="teacher-schedule-day">{weekdays[lesson.weekday]}</span>
                    <span className="teacher-schedule-time">{lesson.start_time.slice(0, 5)}–{lesson.end_time.slice(0, 5)}</span>
                    <span className="teacher-schedule-subject"><strong>{lesson.subject}</strong><small>{lesson.class_name}</small></span>
                    <span className="teacher-schedule-room">{lesson.room || "Room not set"}</span>
                  </article>
                ))}
              </div>
            )}
          </section>

          <section className="panel teacher-classes-panel">
            <header className="teacher-classes-heading">
              <div><h2>Today’s classes</h2><p>Assigned class sections you can work with today.</p></div>
              <span>{classes.length} assigned</span>
            </header>
            <p className="teacher-dashboard-hint">Your assigned classes and quick links for offline-capable attendance and grade entry.</p>
            {classes.length === 0 ? (
              <div className="teacher-dashboard-empty">
                <GraduationCap size={25} />
                <strong>No classes assigned</strong>
                <span>Ask your school administrator to assign classes to your account.</span>
              </div>
            ) : (
              <div className="teacher-class-list">
                {classes.map((classSection) => (
                  <article className="teacher-class-card" key={classSection.id}>
                    <span className="teacher-class-icon"><GraduationCap size={20} /></span>
                    <div className="teacher-class-details">
                      <strong>{classSection.name}</strong>
                      <span>{classSection.academic_year} · {classSection.student_count} students</span>
                    </div>
                    <div className="teacher-class-actions">
                      <Link className="button button-secondary" href={`/attendance?class=${encodeURIComponent(classSection.id)}`}>
                        <ClipboardCheck size={15} /> Attendance
                      </Link>
                      <Link className="button button-primary" href={`/grades?class=${encodeURIComponent(classSection.id)}`}>
                        <BookOpen size={15} /> Grades
                      </Link>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>
          <footer className="student-page-footer">Attendance and grade changes can be saved on this device while offline and sync when you reconnect.</footer>
        </div>
      </main>
      <FeedbackModal message={error} tone="error" onClose={() => setError("")} />
    </div>
  );
}
