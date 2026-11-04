"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import type { ChangeEvent } from "react";
import { ArrowLeft, BookOpen, ClipboardCheck, FileText, GraduationCap, RotateCw, Wifi, WifiOff } from "lucide-react";
import { schoolApi } from "../lib/school-api";
import FeedbackModal from "./FeedbackModal";
import {
  attendanceTenant,
  cacheAccount,
  cacheClasses,
  cacheRoster,
  getCachedAccount,
  getCachedClasses,
  getCachedRoster,
  getLocalAttendance,
  getLocalAttendanceForUser,
  retryFailedAttendance,
  saveAttendanceOffline,
  syncPendingAttendance,
  type AttendanceRoster,
  type AttendanceStatus,
  type CachedAccount,
  type ClassSection,
  type LocalAttendance,
  type RosterStudent,
} from "../lib/offline-attendance";

const statusLabels: Record<AttendanceStatus, string> = {
  present: "Present",
  absent: "Absent",
  late: "Late",
  excused: "Excused",
};

function localDate(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function mergeLocalAttendance(
  roster: AttendanceRoster,
  records: LocalAttendance[],
): AttendanceRoster {
  const localRecords = new Map(records.map((record) => [record.student_id, record]));
  return {
    ...roster,
    students: roster.students.map((student) => {
      const local = localRecords.get(student.student_id);
      if (!local) return student;
      const localTime = Date.parse(local.client_timestamp);
      const serverTime = student.client_timestamp
        ? Date.parse(student.client_timestamp)
        : Number.NEGATIVE_INFINITY;
      if (localTime < serverTime) return student;
      return {
        ...student,
        attendance_status: local.status,
        client_timestamp: local.client_timestamp,
        local_id: local.local_id,
        sync_state: local.sync_state,
        sync_error: local.sync_error,
      };
    }),
  };
}

export default function AttendanceWorkspace() {
  const [tenant, setTenant] = useState("");
  const [account, setAccount] = useState<CachedAccount | null>(null);
  const [classes, setClasses] = useState<ClassSection[]>([]);
  const [classId, setClassId] = useState("");
  const [attendanceDate, setAttendanceDate] = useState(localDate);
  const [roster, setRoster] = useState<AttendanceRoster | null>(null);
  const [loading, setLoading] = useState(true);
  const [rosterLoading, setRosterLoading] = useState(false);
  const [online, setOnline] = useState(true);
  const [syncBusy, setSyncBusy] = useState(false);
  const [pendingCount, setPendingCount] = useState(0);
  const [failedCount, setFailedCount] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    async function initialize() {
      if (!window.sessionStorage.getItem("northstar_access_token")) {
        window.location.replace("/login");
        return;
      }

      let tenantSubdomain: string;
      try {
        tenantSubdomain = attendanceTenant();
      } catch (tenantError: unknown) {
        setError(
          tenantError instanceof Error
            ? tenantError.message
            : "Open attendance on this school's tenant subdomain.",
        );
        setLoading(false);
        return;
      }
      setTenant(tenantSubdomain);
      setOnline(navigator.onLine);
      const requestedClass = new URLSearchParams(window.location.search).get("class");
      let cachedAccount: CachedAccount | undefined;
      let cachedClasses: ClassSection[] | undefined;
      try {
        [cachedAccount, cachedClasses] = await Promise.all([
          getCachedAccount(tenantSubdomain),
          getCachedClasses(tenantSubdomain),
        ]);
      } catch (storageError: unknown) {
        if (active) {
          setError(
            storageError instanceof Error
              ? storageError.message
              : "Browser offline storage is unavailable.",
          );
        }
      }

      try {
        const currentAccount = await schoolApi<CachedAccount>("/api/v1/auth/me");
        const currentClasses = await schoolApi<ClassSection[]>(
          currentAccount.role === "teacher"
            ? "/api/v1/teacher/classes"
            : "/api/v1/classes",
        );
        if (!active) return;
        await Promise.all([
          cacheAccount(tenantSubdomain, currentAccount),
          cacheClasses(tenantSubdomain, currentClasses),
        ]);
        setAccount(currentAccount);
        setClasses(currentClasses);
        setClassId((current) => {
          if (current && currentClasses.some((item) => item.id === current)) return current;
          if (requestedClass && currentClasses.some((item) => item.id === requestedClass)) return requestedClass;
          return currentClasses[0]?.id ?? "";
        });
      } catch (loadError: unknown) {
        if (!active) return;
        if (!window.sessionStorage.getItem("northstar_access_token")) {
          window.location.replace("/login");
          return;
        }
        if (cachedAccount && cachedClasses) {
          setAccount(cachedAccount);
          setClasses(cachedClasses);
          setClassId(
            cachedClasses.some((item) => item.id === requestedClass)
              ? requestedClass ?? ""
              : cachedClasses[0]?.id ?? "",
          );
          setNotice("Offline mode. Cached classes are available; new marks will sync later.");
        } else {
          setError(
            loadError instanceof Error
              ? `${loadError.message} Connect once to load classes for offline use.`
              : "Unable to load classes for this school.",
          );
        }
      } finally {
        if (active) setLoading(false);
      }
    }

    void initialize();
    return () => {
      active = false;
    };
  }, []);

  const loadRoster = useCallback(async () => {
    if (!tenant || !classId || !account) {
      setRoster(null);
      return;
    }
    setRosterLoading(true);
    setError("");
    let cachedRoster: AttendanceRoster | undefined;
    try {
      cachedRoster = await getCachedRoster(tenant, classId, attendanceDate);
    } catch (storageError: unknown) {
      setError(
        storageError instanceof Error
          ? storageError.message
          : "Browser offline storage is unavailable.",
      );
      setRosterLoading(false);
      return;
    }
    try {
      const serverRoster = await schoolApi<AttendanceRoster>(
        `/api/v1/classes/${encodeURIComponent(classId)}/attendance-roster?attendance_date=${encodeURIComponent(attendanceDate)}`,
      );
      await cacheRoster(tenant, serverRoster);
      const localRecords = await getLocalAttendance(tenant, classId, attendanceDate);
      setRoster(mergeLocalAttendance(serverRoster, localRecords));
    } catch (loadError: unknown) {
      if (!window.sessionStorage.getItem("northstar_access_token")) {
        window.location.replace("/login");
        return;
      }
      const localRecords = await getLocalAttendance(tenant, classId, attendanceDate);
      if (cachedRoster) {
        setRoster(mergeLocalAttendance(cachedRoster, localRecords));
        setNotice("Showing the saved class roster. Attendance will sync when online.");
      } else {
        setRoster(null);
        setError(
          loadError instanceof Error
            ? `${loadError.message} This class roster has not been cached on this device yet.`
            : "Unable to load the class roster.",
        );
      }
    } finally {
      setRosterLoading(false);
    }
  }, [account, attendanceDate, classId, tenant]);

  useEffect(() => {
    void loadRoster();
  }, [loadRoster]);

  const refreshQueueCounts = useCallback(async () => {
    if (!tenant || !account) return;
    const localRecords = await getLocalAttendanceForUser(tenant, account.id);
    setPendingCount(localRecords.filter((record) => record.sync_state === "pending").length);
    setFailedCount(localRecords.filter((record) => record.sync_state === "failed").length);
  }, [account, tenant]);

  const syncQueue = useCallback(async () => {
    if (!tenant || !account || !navigator.onLine || syncBusy) return;
    setSyncBusy(true);
    try {
      const report = await syncPendingAttendance(tenant, account.id);
      await refreshQueueCounts();
      await loadRoster();
      if (report.last_error) {
        setError(report.last_error);
      } else if (report.synced > 0) {
        setNotice(`${report.synced} attendance mark${report.synced === 1 ? "" : "s"} synced.`);
      }
    } catch (syncError: unknown) {
      setError(
        syncError instanceof Error ? syncError.message : "Unable to sync attendance.",
      );
    } finally {
      setSyncBusy(false);
    }
  }, [account, loadRoster, refreshQueueCounts, syncBusy, tenant]);

  useEffect(() => {
    if (!account || !tenant) return;
    void refreshQueueCounts();
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

  async function markAttendance(
    student: RosterStudent,
    event: ChangeEvent<HTMLSelectElement>,
  ) {
    const nextStatus = event.target.value as AttendanceStatus | "";
    if (!roster || !account || !tenant || !nextStatus) return;
    setError("");
    try {
      const record = await saveAttendanceOffline(tenant, account.id, {
        student_id: student.student_id,
        class_section_id: roster.class_section_id,
        attendance_date: roster.attendance_date,
        status: nextStatus,
      });
      setRoster((current) => current && ({
        ...current,
        students: current.students.map((item) =>
          item.student_id === student.student_id
            ? {
                ...item,
                attendance_status: nextStatus,
                client_timestamp: record.client_timestamp,
                local_id: record.local_id,
                sync_state: "pending",
                sync_error: undefined,
              }
            : item,
        ),
      }));
      setNotice(`${student.first_name} ${student.last_name}: ${statusLabels[nextStatus]} saved on this device.`);
      await refreshQueueCounts();
      if (navigator.onLine) void syncQueue();
    } catch (saveError: unknown) {
      setError(
        saveError instanceof Error
          ? saveError.message
          : "Attendance could not be saved to this device.",
      );
    }
  }

  async function retryFailed() {
    if (!tenant || !account) return;
    await retryFailedAttendance(tenant, account.id);
    await refreshQueueCounts();
    await syncQueue();
  }

  if (loading) {
    return <main className="workspace-loading" aria-live="polite">Loading attendance workspace…</main>;
  }

  const schoolName = tenant.replace(/-/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
  const markedCount = roster?.students.filter((student) => student.attendance_status).length ?? 0;

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
        <div className="student-side-link active"><ClipboardCheck size={17} /> Attendance</div>
        <Link className="student-side-link" href="/grades"><BookOpen size={17} /> Grades</Link>
        <Link className="student-side-link" href="/report-cards"><FileText size={17} /> Report cards</Link>
        <div className="student-account">
          <span className="student-account-avatar">{account?.email[0]?.toUpperCase() ?? "A"}</span>
          <span><strong>{account?.email}</strong><small>{account?.role === "teacher" ? "Teacher" : "School administrator"}</small></span>
        </div>
      </aside>

      <main className="student-main">
        <header className="student-topbar">
          <div><span>Workspace</span><b>/</b><strong>Attendance</strong></div>
          <div className={`attendance-connection${online ? " online" : ""}`}>
            {online ? <Wifi size={15} /> : <WifiOff size={15} />}
            {online ? "Online" : "Offline"}
          </div>
        </header>
        <div className="student-page-content">
          <section className="student-page-heading">
            <div>
              <p className="eyebrow">DAILY CLASS ROSTER</p>
              <h1>Attendance</h1>
              <p className="page-description">Marks save on this device first and sync when connected.</p>
            </div>
            <div className="student-page-actions">
              <button className="button button-secondary" disabled={!online || syncBusy || pendingCount === 0} onClick={() => void syncQueue()}>
                <RotateCw size={15} className={syncBusy ? "attendance-spin" : ""} />
                {syncBusy ? "Syncing…" : `Sync${pendingCount ? ` (${pendingCount})` : ""}`}
              </button>
              {failedCount > 0 && <button className="button button-secondary" disabled={!online || syncBusy} onClick={() => void retryFailed()}>Retry failed ({failedCount})</button>}
            </div>
          </section>

          <section className="panel attendance-roster-panel">
            <div className="attendance-controls">
              <label>Class section
                <select value={classId} onChange={(event) => setClassId(event.target.value)} disabled={classes.length === 0}>
                  {classes.length === 0 && <option value="">No class sections available</option>}
                  {classes.map((classSection) => <option key={classSection.id} value={classSection.id}>{classSection.name} · {classSection.academic_year}</option>)}
                </select>
              </label>
              <label>Attendance date
                <input type="date" value={attendanceDate} onChange={(event) => setAttendanceDate(event.target.value)} />
              </label>
              <div className="attendance-summary"><strong>{markedCount} / {roster?.students.length ?? 0}</strong><span>marked</span></div>
            </div>
            {classes.length === 0 && <p className="attendance-notice">Create a class section before taking attendance. <Link href="/classes">Open Classes</Link>.</p>}
            <div className="table-scroll">
              <table className="student-record-table attendance-table">
                <thead><tr><th>STUDENT</th><th>ADMISSION NO.</th><th>ATTENDANCE</th><th>SYNC STATUS</th></tr></thead>
                <tbody>
                  {roster?.students.map((student) => (
                    <tr key={student.student_id}>
                      <td><strong className="class-table-name">{student.first_name} {student.last_name}</strong></td>
                      <td>{student.admission_number}</td>
                      <td>
                        <select
                          className={`attendance-select${student.attendance_status ? ` status-${student.attendance_status}` : ""}`}
                          aria-label={`Attendance for ${student.first_name} ${student.last_name}`}
                          value={student.attendance_status ?? ""}
                          onChange={(event) => void markAttendance(student, event)}
                        >
                          {!student.attendance_status && <option value="">Not marked</option>}
                          {Object.entries(statusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                        </select>
                      </td>
                      <td>
                        {!student.attendance_status ? <span className="class-name">—</span> : (
                          <span className={`attendance-sync-state ${student.sync_state ?? "synced"}`}>
                            {student.sync_state === "pending" ? "Pending sync" : student.sync_state === "failed" ? "Needs attention" : "Synced"}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                  {roster && roster.students.length === 0 && <tr><td colSpan={4} className="empty-row">No active students are enrolled in this class.</td></tr>}
                  {!roster && !error && <tr><td colSpan={4} className="empty-row">{rosterLoading ? "Loading roster…" : "Select a class to load its roster."}</td></tr>}
                </tbody>
              </table>
            </div>
            <footer className="attendance-footer">
              <span>{roster ? `${roster.class_name} · ${roster.academic_year}` : "Class roster"}</span>
              <span>{pendingCount} pending · {failedCount} failed</span>
            </footer>
          </section>
          <footer className="student-page-footer">Attendance is stored in this browser’s IndexedDB before sync. Pending marks are isolated by school and the signing-in administrator.</footer>
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
