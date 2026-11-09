"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import {
  ArrowLeft,
  Archive,
  BookOpen,
  ClipboardCheck,
  FileText,
  Download,
  GraduationCap,
  Mail,
  Pencil,
  Plus,
  Search,
  Upload,
  X,
} from "lucide-react";
import { schoolApi, type Student, type StudentInput, type StudentList } from "../lib/school-api";
import FeedbackModal from "./FeedbackModal";

type Account = {
  email: string;
  role: string;
};

type ClassSection = {
  id: string;
  name: string;
  academic_year: string;
  is_active: boolean;
};

const emptyStudent: StudentInput = {
  admission_number: "",
  first_name: "",
  last_name: "",
  date_of_birth: "",
  gender: "prefer_not_to_say",
  class_section_id: "",
  admission_status: "new",
  guardian_name: "",
  guardian_email: null,
  guardian_phone: "",
  medical_notes: null,
};

const csvTemplate =
  "admission_number,first_name,last_name,date_of_birth,gender,class_name,academic_year,admission_status,guardian_name,guardian_email,guardian_phone,medical_notes\r\n" +
  "GR-001,Amina,Okafor,2015-05-04,female,Grade 5A,2026-2027,new,Ada Okafor,ada@example.com,+2348000000000,\r\n";

async function fetchStudentPage(
  includeInactive: boolean,
  offset: number,
  search: string,
): Promise<StudentList> {
  const params = new URLSearchParams({
    include_inactive: String(includeInactive),
    offset: String(offset),
    limit: "100",
  });
  if (search.trim()) params.set("search", search.trim());
  return schoolApi<StudentList>(`/api/v1/students?${params.toString()}`);
}

function initials(student: Student): string {
  return `${student.first_name[0] ?? ""}${student.last_name[0] ?? ""}`.toUpperCase();
}

export default function StudentWorkspace() {
  const [students, setStudents] = useState<Student[]>([]);
  const [classes, setClasses] = useState<ClassSection[]>([]);
  const [account, setAccount] = useState<Account | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [recordsUnavailable, setRecordsUnavailable] = useState(false);
  const [search, setSearch] = useState("");
  const [showInactive, setShowInactive] = useState(false);
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [showAdmissionForm, setShowAdmissionForm] = useState(false);
  const [editingStudentId, setEditingStudentId] = useState<string | null>(null);
  const [form, setForm] = useState<StudentInput>(emptyStudent);
  const [saving, setSaving] = useState(false);
  const [importing, setImporting] = useState(false);
  const [invitingGuardianId, setInvitingGuardianId] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [formError, setFormError] = useState("");

  useEffect(() => {
    let active = true;
    async function loadWorkspace() {
      if (!window.sessionStorage.getItem("northstar_access_token")) {
        window.location.replace("/login");
        return;
      }
      try {
        const [currentAccount, classSections] = await Promise.all([
          schoolApi<Account>("/api/v1/auth/me"),
          schoolApi<ClassSection[]>("/api/v1/classes"),
        ]);
        if (active) {
          setAccount(currentAccount);
          setClasses(classSections.filter((item) => item.is_active));
        }
      } catch (error: unknown) {
        if (!active) return;
        if (!window.sessionStorage.getItem("northstar_access_token")) {
          window.location.replace("/login");
          return;
        }
        setLoadError(
          error instanceof Error ? error.message : "Unable to load the school workspace.",
        );
        setRecordsUnavailable(true);
      } finally {
        if (active) setLoading(false);
      }
    }
    void loadWorkspace();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!account) return;
    let active = true;
    const timer = window.setTimeout(() => {
      void fetchStudentPage(showInactive, offset, search)
        .then((result) => {
          if (!active) return;
          setStudents(result.items);
          setTotal(result.total);
          setLoadError("");
          setRecordsUnavailable(false);
          setLoading(false);
        })
        .catch((error: unknown) => {
          if (!active) return;
          setLoadError(
            error instanceof Error ? error.message : "Unable to load student records.",
          );
          setRecordsUnavailable(true);
          setLoading(false);
        });
    }, search ? 250 : 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [account, offset, search, showInactive]);

  function updateForm<K extends keyof StudentInput>(key: K, value: StudentInput[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function startAdmission() {
    setEditingStudentId(null);
    setForm(emptyStudent);
    setFormError("");
    setShowAdmissionForm(true);
  }

  function startEditing(student: Student) {
    setEditingStudentId(student.id);
    setForm({
      admission_number: student.admission_number,
      first_name: student.first_name,
      last_name: student.last_name,
      date_of_birth: student.date_of_birth,
      gender: student.gender,
      class_section_id: classes.find(
        (item) => item.name === student.class_name && item.academic_year === student.academic_year,
      )?.id ?? "",
      admission_status: student.admission_status,
      guardian_name: student.guardian_name,
      guardian_email: student.guardian_email,
      guardian_phone: student.guardian_phone,
      medical_notes: student.medical_notes,
    });
    setFormError("");
    setShowAdmissionForm(true);
  }

  async function submitAdmission(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setFormError("");
    try {
      const student = await schoolApi<Student>(
        editingStudentId
          ? `/api/v1/students/${editingStudentId}`
          : "/api/v1/students",
        {
        method: editingStudentId ? "PATCH" : "POST",
        body: JSON.stringify({
          ...form,
          guardian_email: form.guardian_email?.trim() || null,
          medical_notes: form.medical_notes?.trim() || null,
        }),
        },
      );
      if (editingStudentId) {
        setStudents((current) =>
          current.map((item) => (item.id === student.id ? student : item)),
        );
      } else {
        setOffset(0);
        setSearch("");
        setShowInactive(false);
        setStudents((current) => [student, ...current]);
        setTotal((current) => current + 1);
      }
      setShowAdmissionForm(false);
      setEditingStudentId(null);
      setForm(emptyStudent);
      setNotice(
        editingStudentId
          ? `${student.first_name} ${student.last_name} was updated.`
          : `${student.first_name} ${student.last_name} was admitted.`,
      );
    } catch (error: unknown) {
      setFormError(error instanceof Error ? error.message : "Student admission failed.");
    } finally {
      setSaving(false);
    }
  }

  async function importCsv(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (file.size > 2_000_000) {
      setLoadError("CSV imports must be 2 MB or smaller.");
      return;
    }

    setImporting(true);
    setLoadError("");
    try {
      const imported = await schoolApi<{ imported: number }>("/api/v1/students/import", {
        method: "POST",
        headers: { "Content-Type": "text/csv" },
        body: await file.text(),
      });
      const page = await fetchStudentPage(showInactive, offset, search);
      setStudents(page.items);
      setTotal(page.total);
      setNotice(`${imported.imported} student${imported.imported === 1 ? "" : "s"} imported.`);
    } catch (error: unknown) {
      setLoadError(error instanceof Error ? error.message : "Student import failed.");
    } finally {
      setImporting(false);
    }
  }

  async function toggleActive(student: Student) {
    const nextActive = !student.is_active;
    try {
      const updated = await schoolApi<Student>(`/api/v1/students/${student.id}`, {
        method: "PATCH",
        body: JSON.stringify({ is_active: nextActive }),
      });
      setStudents((current) =>
        current
          .map((item) => (item.id === updated.id ? updated : item))
          .filter((item) => showInactive || item.is_active),
      );
      if (!showInactive) setTotal((current) => Math.max(0, current + (nextActive ? 1 : -1)));
      setNotice(
        `${student.first_name} ${student.last_name} ${nextActive ? "restored" : "archived"}.`,
      );
    } catch (error: unknown) {
      setLoadError(error instanceof Error ? error.message : "Student update failed.");
    }
  }

  async function inviteGuardian(student: Student) {
    if (!student.guardian_email) return;
    setInvitingGuardianId(student.id);
    setLoadError("");
    setNotice("");
    try {
      await schoolApi<{ message: string }>(
        `/api/v1/students/${encodeURIComponent(student.id)}/guardian-invitation`,
        { method: "POST" },
      );
      setNotice(`Parent portal invitation sent to ${student.guardian_email}.`);
    } catch (error: unknown) {
      setLoadError(error instanceof Error ? error.message : "Guardian invitation failed.");
    } finally {
      setInvitingGuardianId(null);
    }
  }

  if (loading) {
    return <main className="workspace-loading" aria-live="polite">Loading school workspace…</main>;
  }

  const schoolName = typeof window === "undefined" ? "" : window.location.hostname.split(".")[0]
    .replace(/-/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());

  return (
    <div className="student-workspace">
      <aside className="student-sidebar">
        <Link className="brand student-brand" href="/">
          <span className="brand-mark"><GraduationCap size={21} /></span>
          <span>
            <span className="brand-name">northstar</span>
            <span className="brand-caption">SCHOOL MANAGEMENT</span>
          </span>
        </Link>
        <div className="student-school-name">{schoolName || "School workspace"}</div>
        <div className="nav-label">WORKSPACE</div>
        <Link className="student-side-link" href="/"><ArrowLeft size={17} /> Overview</Link>
        <div className="student-side-link active"><GraduationCap size={17} /> Students</div>
        <Link className="student-side-link" href="/classes"><GraduationCap size={17} /> Classes</Link>
        <Link className="student-side-link" href="/attendance"><ClipboardCheck size={17} /> Attendance</Link>
        <Link className="student-side-link" href="/grades"><BookOpen size={17} /> Grades</Link>
        <Link className="student-side-link" href="/report-cards"><FileText size={17} /> Report cards</Link>
        <div className="student-account">
          <span className="student-account-avatar">{account?.email[0]?.toUpperCase() ?? "A"}</span>
          <span><strong>{account?.email}</strong><small>School administrator</small></span>
        </div>
      </aside>

      <main className="student-main">
        <header className="student-topbar">
          <div><span>Workspace</span><b>/</b><strong>Students</strong></div>
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
              <p className="eyebrow">STUDENT ADMISSION</p>
              <h1>Students</h1>
              <p className="page-description">Manage admissions, class assignments, and guardian contacts.</p>
            </div>
            <div className="student-page-actions">
              <a className="button button-secondary" href={`data:text/csv;charset=utf-8,${encodeURIComponent(csvTemplate)}`} download="northstar-students-template.csv">
                <Download size={15} /> CSV template
              </a>
              <label className={`button button-secondary${importing ? " button-disabled" : ""}`}>
                <Upload size={15} /> {importing ? "Importing…" : "Import CSV"}
                <input type="file" accept=".csv,text/csv" onChange={importCsv} disabled={importing} hidden />
              </label>
              <button className="button button-primary" onClick={startAdmission}>
                <Plus size={16} /> Admit student
              </button>
            </div>
          </section>

          <section className="student-metrics" aria-label="Student counts">
            <article><span>{showInactive ? "Matching records" : "Active student records"}</span><strong>{total}</strong></article>
            <article><span>Loaded on this page</span><strong>{students.length}</strong></article>
            <article><span>Page</span><strong>{Math.floor(offset / 100) + 1}</strong></article>
          </section>

          <section className="panel student-records-panel">
            <div className="student-records-toolbar">
              <div>
                <h2>Student records</h2>
                <p>Admission and guardian details for this school.</p>
              </div>
              <div className="student-record-controls">
                <label className="search-field">
                  <Search size={16} />
                  <input value={search} onChange={(event) => { setSearch(event.target.value); setOffset(0); }} placeholder="Search students…" aria-label="Search students" />
                </label>
                <label className="inactive-toggle">
                  <input type="checkbox" checked={showInactive} onChange={(event) => { setShowInactive(event.target.checked); setOffset(0); }} />
                  Include archived
                </label>
              </div>
            </div>

            <div className="table-scroll">
              <table className="student-record-table">
                <thead>
                  <tr><th>STUDENT</th><th>ADMISSION NO.</th><th>CLASS / YEAR</th><th>GUARDIAN</th><th>STATUS</th><th /></tr>
                </thead>
                <tbody>
                  {students.map((student) => (
                    <tr key={student.id}>
                      <td><div className="student-cell"><span className="avatar lilac">{initials(student)}</span><span><strong>{student.first_name} {student.last_name}</strong><small>{student.date_of_birth}</small></span></div></td>
                      <td><span className="class-name">{student.admission_number}</span></td>
                      <td><span className="class-name">{student.class_name}</span><small className="record-subtext">{student.academic_year}</small></td>
                      <td><span className="guardian-cell"><strong>{student.guardian_name}</strong><small>{student.guardian_email || student.guardian_phone}</small></span></td>
                      <td>
                        <span className={`record-status${student.is_active ? " is-active" : ""}`}>{student.is_active ? "Active" : "Archived"}</span>
                        <small className="record-subtext">{student.admission_status.replace(/_/g, " ")}</small>
                      </td>
                      <td className="student-row-actions">
                        <button
                          className="archive-student"
                          onClick={() => void inviteGuardian(student)}
                          aria-label={`Invite ${student.guardian_name} to the parent portal`}
                          title={student.guardian_email ? `Invite ${student.guardian_email}` : "Add a guardian email before inviting"}
                          disabled={!student.is_active || !student.guardian_email || invitingGuardianId !== null}
                        >
                          <Mail size={15} />
                        </button>
                        <button className="archive-student" onClick={() => startEditing(student)} aria-label={`Edit ${student.first_name} ${student.last_name}`}><Pencil size={15} /></button>
                        <button className="archive-student" onClick={() => void toggleActive(student)} aria-label={`${student.is_active ? "Archive" : "Restore"} ${student.first_name} ${student.last_name}`}><Archive size={16} /></button>
                      </td>
                    </tr>
                  ))}
                  {students.length === 0 && (
                    <tr><td colSpan={6} className="empty-row">{recordsUnavailable ? "Student records could not be loaded." : search ? "No students match your search." : "No student records yet. Admit a student or import a CSV to get started."}</td></tr>
                  )}
                </tbody>
              </table>
            </div>
            <div className="student-table-footer">
              <span>Showing {total === 0 ? 0 : offset + 1}–{Math.min(offset + students.length, total)} of {total} records</span>
              <div className="pagination">
                <button aria-label="Previous page" disabled={offset === 0} onClick={() => setOffset((current) => Math.max(0, current - 100))}>Previous</button>
                <button aria-label="Next page" disabled={offset + students.length >= total} onClick={() => setOffset((current) => current + 100)}>Next</button>
              </div>
            </div>
          </section>
          <footer className="student-page-footer">Student data belongs to this school and is isolated from other tenants.</footer>
        </div>
      </main>

      {showAdmissionForm && (
        <div className="student-modal-backdrop" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setShowAdmissionForm(false);
        }}>
          <section className="student-modal" role="dialog" aria-modal="true" aria-labelledby="student-modal-title">
            <header><div><p className="eyebrow">{editingStudentId ? "UPDATE RECORD" : "NEW RECORD"}</p><h2 id="student-modal-title">{editingStudentId ? "Edit student record" : "Admit a student"}</h2></div><button className="icon-button" aria-label="Close form" onClick={() => setShowAdmissionForm(false)}><X size={19} /></button></header>
            <form onSubmit={submitAdmission}>
              <div className="student-form-grid">
                <label>Admission number<input required maxLength={48} value={form.admission_number} onChange={(event) => updateForm("admission_number", event.target.value)} /></label>
                <label>Date of birth<input required type="date" value={form.date_of_birth} onChange={(event) => updateForm("date_of_birth", event.target.value)} /></label>
                <label>First name<input required maxLength={80} value={form.first_name} onChange={(event) => updateForm("first_name", event.target.value)} /></label>
                <label>Last name<input required maxLength={80} value={form.last_name} onChange={(event) => updateForm("last_name", event.target.value)} /></label>
                <label>Gender<select value={form.gender} onChange={(event) => updateForm("gender", event.target.value)}><option value="prefer_not_to_say">Prefer not to say</option><option value="female">Female</option><option value="male">Male</option><option value="other">Other</option></select></label>
                <label className="student-form-wide">Class / section · Academic year
                  <select required value={form.class_section_id} onChange={(event) => updateForm("class_section_id", event.target.value)} disabled={classes.length === 0}>
                    <option value="">{classes.length ? "Select an existing class section" : "Create a class section first"}</option>
                    {classes.map((classSection) => (
                      <option key={classSection.id} value={classSection.id}>
                        {classSection.name} · {classSection.academic_year}
                      </option>
                    ))}
                  </select>
                </label>
                <label>Admission status<select value={form.admission_status} onChange={(event) => updateForm("admission_status", event.target.value as StudentInput["admission_status"])}>
                  <option value="new">New</option>
                  <option value="in_review">In review</option>
                  <option value="accepted">Accepted</option>
                  <option value="enrolled">Enrolled</option>
                  <option value="waitlist">Waitlist</option>
                  <option value="withdrawn">Withdrawn</option>
                </select></label>
                <label>Guardian name<input required maxLength={160} value={form.guardian_name} onChange={(event) => updateForm("guardian_name", event.target.value)} /></label>
                <label>Guardian email<input type="email" maxLength={254} value={form.guardian_email ?? ""} onChange={(event) => updateForm("guardian_email", event.target.value)} /></label>
                <label>Guardian phone<input required maxLength={32} value={form.guardian_phone} onChange={(event) => updateForm("guardian_phone", event.target.value)} /></label>
                <label className="student-form-wide">Medical notes<textarea maxLength={2000} rows={3} value={form.medical_notes ?? ""} onChange={(event) => updateForm("medical_notes", event.target.value)} /></label>
              </div>
              <footer><button className="button button-secondary" type="button" onClick={() => setShowAdmissionForm(false)}>Cancel</button><button className="button button-primary" type="submit" disabled={saving || classes.length === 0 || !form.class_section_id}>{saving ? "Saving…" : editingStudentId ? "Save changes" : "Save student"}</button></footer>
            </form>
          </section>
        </div>
      )}
      <FeedbackModal
        message={formError || loadError || notice}
        tone={formError || loadError ? "error" : "success"}
        onClose={() => {
          setFormError("");
          setLoadError("");
          setNotice("");
        }}
      />
    </div>
  );
}
